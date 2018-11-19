from bs4 import BeautifulSoup as soup
from bz2 import BZ2File
from collections import Counter
from copy import deepcopy
from datetime import datetime as dt, timedelta
from dateutil.parser import parse as parse_date
from etk.extractors.date_extractor import DateExtractor
from etk.extractors.spacy_ner_extractor import SpacyNerExtractor
from flask import Flask, send_from_directory, jsonify, request, redirect, url_for, abort
from hashlib import sha256
from json import load, dump, loads, dumps
from math import sqrt, ceil, floor
from nltk import word_tokenize, pos_tag, ne_chunk
from nltk.corpus import stopwords
from numpy import array
from os import makedirs, listdir, rename, remove
from os.path import dirname, abspath, exists, join
from pandas import DataFrame
from pickle import load as pload, dump as pdump
from pprint import pprint
from random import choices, shuffle
from regex import findall, sub, compile, match, DOTALL, MULTILINE, VERBOSE
from requests import get, post, head
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Firefox
from selenium.webdriver.firefox.options import Options
from shutil import rmtree
from sklearn.cluster import KMeans
from sys import stdout
from threading import Thread
from time import strftime, sleep, time
from traceback import print_exc, format_exc
from urllib.parse import urljoin
from xml.etree.cElementTree import iterparse
from wikipediaapi import Wikipedia

# --- constants ---------------------------------------------------------------

PATH_RESOURCES = join(dirname(__file__), 'resources')
PATH_LOG = join(PATH_RESOURCES, 'log_%s.txt')
PATH_ALL_TABLES = join(PATH_RESOURCES, 'all_tables.jsonl')
PATH_HTML_ARTICLES = join(PATH_RESOURCES, 'html_articles')
makedirs(PATH_HTML_ARTICLES, exist_ok=True)

PATTERN_LOG = '[%s] %s\n'

SCRIPT_ADD_RENDER = """
function pathTo(element) {
	if (element === document) return ""
	var ix = 0
	var siblings = element.parentNode.childNodes
	for (var i = 0; i < siblings.length; i++) {
		if (siblings[i] === element) return pathTo(element.parentNode) + '/' + element.tagName + '[' + (ix + 1) + ']'
		if (siblings[i].nodeType === 1 && siblings[i].tagName === element.tagName) ix++
	}
}

var removeElements = []
function addRender(subtree) {
	var style = getComputedStyle(subtree)
	if (subtree.tagName == "TR" && subtree.children.length == 0 || subtree.offsetWidth == undefined || style["display"] == "none" || subtree.tagName == "SUP" && subtree.className == "reference") {
		removeElements.push(subtree)
		return
	}
	var serialStyle = ""
	for (let prop of style) {
		if (prop[0] != "-") {
			serialStyle += prop + ":" + style[prop].replace(/:/g, "") + "|"
		}
	}
	serialStyle += "width:" + subtree.offsetWidth / document.body.offsetWidth + "|height:" + subtree.offsetHeight / document.body.offsetHeight
	if (subtree.tagName == "TD" || subtree.tagName == "TH") {
		serialStyle += "|colspan:" + subtree.colSpan + "|rowspan:" + subtree.rowSpan
	}
	subtree.setAttribute("data-computed-style", serialStyle)
	subtree.setAttribute("data-xpath", pathTo(subtree).toLowerCase())
	for (let child of subtree.children) addRender(child)
}

function preprocess() {
	var elements = document.querySelectorAll(injected_script_selector)
	for (let subtree of elements) addRender(subtree)
	for (let elem of removeElements) elem.remove()
}

const injected_script_selector = arguments[0]

if (document.readyState == 'complete') {
	preprocess()
} else {
	window.onload = function(){preprocess()}
}
"""

# --- import directives -------------------------------------------------------

makedirs(PATH_RESOURCES, exist_ok=True)


# --- format ------------------------------------------------------------------

def date_stamp():
    ''' Return the current timestamp. '''
    return strftime('%Y-%m-%d, %H:%M:%S')


def bytes_to_human(size, decimal_places=2):
    ''' Returns a human readable file size from a number of bytes. '''
    for unit in ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']:
        if size < 1024: break
        size /= 1024
    return f'{size:.{decimal_places}f}{unit}B'


def seconds_to_human(seconds):
    ''' Returns a human readable string from a number of seconds. '''
    return str(timedelta(seconds=int(seconds))).zfill(8)


# --- log ---------------------------------------------------------------------

def log(log_name, text):
    ''' Logs the given text to the log specified, and prints it. '''
    text = PATTERN_LOG % (date_stamp(), text)
    print('[%s] %s' % (log_name, text), end='')
    with open(PATH_LOG % log_name, 'a', encoding='utf-8') as fp:
        fp.write(text)


def log_error():
    ''' Used inside an except sentence, logs the error to the error log. '''
    log('error', format_exc())


def cache(target, args, identifier=None, cache_life=3 * 24 * 3600):
    ''' Run the target function with the given args, and store it to a pickled
    cache folder using the given identifier or the name of the function. The
    next time it is executed, the cached output is returned unless cache_life
    time expires. '''
    if identifier == None: identifier = target.__name__
    identifier = sub(r'[/\\\*;\[\]\':=,<>]', '_', identifier)
    path = join(PATH_RESOURCES, f'.pickled/{identifier}.pk')
    makedirs(dirname(path), exist_ok=True)
    now = time()
    if exists(path):
        with open(path, 'rb') as fp:
            save_time, value = pload(fp)
        if now - save_time <= cache_life:
            return value
    res = target(*args)
    with open(path, 'wb') as fp:
        pdump((now, res), fp, protocol=3)
    return res


# --- network -----------------------------------------------------------------

def download_file(url, path=None, chunk_size=10 ** 5):
    ''' Downloads a file keeping track of the progress. '''
    if path == None: path = url.split('/')[-1]
    r = get(url, stream=True)
    total_bytes = int(r.headers.get('content-length'))
    bytes_downloaded = 0
    start = time()
    print('Downloading %s (%s)' % (url, bytes_to_human(total_bytes)))
    with open(path, 'wb') as fp:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if not chunk: continue
            fp.write(chunk)
            bytes_downloaded += len(chunk)
            percent = bytes_downloaded / total_bytes
            bar = ('█' * int(percent * 32)).ljust(32)
            time_delta = time() - start
            eta = seconds_to_human((total_bytes - bytes_downloaded) * time_delta / bytes_downloaded)
            avg_speed = bytes_to_human(bytes_downloaded / time_delta).rjust(9)
            stdout.flush()
            stdout.write('\r  %6.02f%% |%s| %s/s eta %s' % (100 * percent, bar, avg_speed, eta))
    print()


_driver = None


def get_driver(headless=True, disable_images=True, open_links_same_tab=False):
    ''' Returns a Firefox webdriver, and run one if there is no any active. '''
    global _driver
    if _driver == None:
        print('Loading Firefox driver')
        opts = Options()
        opts.set_preference('dom.ipc.plugins.enabled.libflashplayer.so', 'false')
        if open_links_same_tab:
            opts.set_preference('browser.link.open_newwindow.restriction', 0)
            opts.set_preference('browser.link.open_newwindow', 1)
        if headless: opts.set_headless()
        if disable_images: opts.set_preference('permissions.default.image', 2)
        _driver = Firefox(options=opts, log_path='NUL', executable_path=join(PATH_RESOURCES, 'geckodriver.exe'))
        _driver.set_page_load_timeout(15)
    return _driver


def close_driver():
    ''' Close the current Firefox webdriver, if any. '''
    global _driver
    if _driver != None:
        print('Closing Firefox driver')
        _driver.close()


def get_with_render(url, render_selector='table', headless=True, disable_images=True, open_links_same_tab=False):
    ''' Downloads a page and renders it to return the page source, the width,
    and the height in pixels. Elements on the subtree selected using
    render_selector contain a data-computed-style attribute and a data-xpath. '''
    driver = get_driver(headless, disable_images, open_links_same_tab)
    driver.get(url)
    driver.execute_script(SCRIPT_ADD_RENDER, render_selector)
    sleep(.5)
    return driver.page_source


# --- vector ------------------------------------------------------------------

def vectors_average(vectors):
    ''' Given a list of mixed feature vectors, returns the average of all them.
    For numerical features, aritmetic average is used. For categorical ones,
    the most common is used. '''
    vectors = [v for v in vectors if len(v)]
    res = {}
    if len(vectors):
        for feat in vectors[0]:
            if type(vectors[0][feat]) == str:
                val = Counter(v[feat] for v in vectors).most_common(1)[0][0]
            else:
                val = sum(v[feat] for v in vectors) / len(vectors)
            res[feat] = val
    return res


def vectors_weighted_average(vectors):
    ''' Given a list of tuples of type <weight, mixed feature vector>, returns
    the weighted average of all them. For numerical features, aritmetic average
    is used. For categorical ones, weighted frequencies are used to return the
    most common. '''
    if len(vectors) == 1: return vectors[0][1]
    res = {}
    total_weight = sum(v[0] for v in vectors)
    if total_weight == 0:
        total_weight = len(vectors)
        for n in range(total_weight):
            vectors[n][0] = 1
    vectors = [(w / total_weight, fs) for w, fs in vectors]
    for f in vectors[0][1]:
        if type(vectors[0][1][f]) == str:
            sum_feat = {}
            for weight, features in vectors:
                if features[f] in sum_feat:
                    sum_feat[features[f]] += weight
                else:
                    sum_feat[features[f]] = weight
            res[f] = max(sum_feat.items(), key=lambda v: v[1])[0]
        else:
            val = 0
            for weight, features in vectors:
                val += weight * features[f]
            res[f] = val
    return res


def vectors_difference(v1, v2, prefix=''):
    ''' Given two mixed feature vectors, return another vector with the
    differences amongst them. For numerical features, absolute value difference
    is computed. For categorical features, Gower distance is used. '''
    res = {}
    for feat in v1:
        if type(v1[feat]) == str:
            res[prefix + feat] = 1 if v1[feat] == v2[feat] else 0
        else:
            res[prefix + feat] = abs(v1[feat] - v2[feat])
    return res


def vector_module(vector):
    ''' Given a mixed feature vector, return the norm of their numerical
    attributes. '''
    nums = [v ** 2 for v in vector.values() if type(v) != str]
    return sqrt(sum(nums))


def binarize_categorical(vectors):
    ''' Given a 2-D list of mixed feature vectors, transform every categorical
    feature into a binary one, using the seen values of all the vectors. '''
    vectors = deepcopy(vectors)
    cat_vector = next([k for k, v in cell.items() if type(v) == str] for row in vectors for cell in row if len(cell))
    for f in cat_vector:
        values = list(set(cell[f] for row in vectors for cell in row if len(cell)))
        for r, row in enumerate(vectors):
            for c, cell in enumerate(row):
                if len(cell) == 0: continue
                for v in values:
                    vectors[r][c][f'{f}-{v}'] = 1 if v == cell[f] else 0
                del vectors[r][c][f]
    return vectors


# --- parsing -----------------------------------------------------------------

_find_dates_extractor = DateExtractor()


def find_dates(text):
    try:
        return parse_date(text, fuzzy_with_tokens=True)[0]
    except:
        pass
    try:
        res = _find_dates_extractor.extract(text, prefer_language_date_order=False)
        if len(res): return res[0].value
    except:
        log('info', f'ETK DateExtractor raised an error on value {text}.')


_find_entities_extractor = SpacyNerExtractor('dummy_parameter')


def find_entities(text):
    try:
        return {ext.value: ext.tag for ext in _find_entities_extractor.extract(text)}
    except:
        log('info', f'ETK SpacyNerExtractor raised an error on value {text}.')
        return dict()