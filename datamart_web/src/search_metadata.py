from datamart.augment import Augment
import pandas as pd
from pandas.api.types import is_object_dtype
import json


class SearchMetadata(object):
    MAX_MATCH = 10

    MAX_DISPLAY_NAMED_ENTITY = 10

    def __init__(self, es_index="datamart_all"):
        self.augument = Augment(es_index=es_index)

    def default_search_by_csv(self, request):

        query_string = request.args.get("query_string", None)
        minimum_should_match_for_column = int(request.args.get(
            "minimum_should_match_for_column")) if "minimum_should_match_for_column" in request.args else None

        df = pd.read_csv(request.files['file']).infer_objects()
        if df is None or df.empty:
            return json.dumps({
                "message": "Failed to create Dataframe from csv, nothing found"
            })

        ret = {
            "message": "Created Dataframe and finding datasets for augmenting",
            "result": []
        }

        query_string_result = self.augument.query_any_field_with_string(
            query_string=query_string) if query_string else None

        for idx in range(df.shape[1]):
            if is_object_dtype(df.iloc[:, idx]):
                this_column_result = self.augument.query_by_column(col=df.iloc[:, idx],
                                                                   minimum_should_match=minimum_should_match_for_column
                                                                   )
                if this_column_result:
                    if not query_string_result:
                        ret["result"].append({
                            "column_idx": idx,
                            "datasets_metadata": this_column_result[:10]
                        })
                    else:
                        ret["result"].append({
                            "column_idx": idx,
                            "datasets_metadata": self.augument.get_metadata_intersection(query_string_result,
                                                                                         this_column_result)
                        })
        return ret
