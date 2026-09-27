from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text as sql_text

GRANULARY = {
    "column": "granulary_name",
    "table": "granulary_works",
    "id": "id_granulary_work",
    "res_table": "granulary_resources"
}

PROCESSED = {
    "column": "processed_name",
    "table": "processed_works",
    "id": "id_processed_work",
    "res_table": "processed_resources"
}

TYPEDLVL2 = {
    "column": "typed_lvl2_name",
    "table": "typed_lvl2_works",
    "id": "id_typed_lvl2_work",
    "res_table": "typed_lvl2_resources"
}


class Schedules:
    GRANULARY: dict[str, str] = {
        "column": "granulary_name",
        "table": "granulary_works",
        "id": "id_granulary_work",
        "res_table": "granulary_resources"
    }

    PROCESSED: dict[str, str] = {
        "column": "processed_name",
        "table": "processed_works",
        "id": "id_processed_work",
        "res_table": "processed_resources"
    }

    TYPEDLVL2: dict[str, str] = {
        "column": "typed_lvl2_name",
        "table": "typed_lvl2_works",
        "id": "id_typed_lvl2_work",
        "res_table": "typed_lvl2_resources"
    }

    def __init__(self, url):
        """Constructor
        Args:
            url (str): link to database service
        """

        self.connect = create_engine(url)

    def from_names(
            self,
            works: list[str],
            resources=None,
            ceil_limit: int = 1_000,
            objects_limit: int = 1,
            crossing=False,
            key=None
    ):
        """method for getting schedules by works names list

        Args:
            work_name_list (list[str]): lists of basic works names
            ceil_limit (int, optional): limit of records in one dataframe. Defaults to 1_000.
        """
        key = key if key is not None else self.GRANULARY
        resources = resources if resources is not None else []
        if len(works) == 0 and len(resources) == 0:
            raise Exception("Empty works list")
        self.ceil_limit = ceil_limit
        self.objects_limit = objects_limit
        self.works_list = works
        self.resource_list = resources

        if crossing:
            self.objects = list(
                set(self._get_objects_by_resource(key)).intersection(set(self._get_objects_by_works(key))))
        else:
            self.objects = list({*self._get_objects_by_resource(key), *self._get_objects_by_works(key)})

        if len(self.objects) == 0:
            raise Exception("Objects not found")

        return self

    def get_all_works_name(self):
        query = f"""
        select DISTINCT name, granulary_name, typed_lvl2_name as lvl2_name, processed_name from names_mapper"""

        df = self.execute_query(query)
        return df

    def get_resources_names(self, res_type=None):
        res_type = res_type if res_type is not None else GRANULARY
        query = f"SELECT DISTINCT {res_type['column']} FROM resources_names_mv"
        df = self.execute_query(query)
        return df

    def get_works_names(self, work_type=None):
        work_type = work_type if work_type is not None else GRANULARY
        query = f"SELECT DISTINCT {work_type['column']} FROM works_names_mv"
        df = self.execute_query(query)
        return df

    def get_all_resources_name(self):
        query = f"""
        select DISTINCT name, granulary_name, typed_lvl2_name as lvl2_name, processed_name from resources_names_mv"""
        df = self.execute_query(query)
        return df

    def get_works_by_pulls(
            self,
            work_pulls: list,
            resource_list: Optional[list] = None,
            key=None,
            path_to_log="empty_pull.txt",
            res_key=None
    ):
        res_key = res_key if res_key is not None else GRANULARY
        resource_list = resource_list if resource_list is not None else []
        key = key if key is not None else GRANULARY
        self.path_to_log = path_to_log
        for pull in work_pulls:
            query = f"""with date_cte (date, object_id)
                as (
                    (select date, object_id from works_names_mv wnm
                    where wnm.{key["column"]} in ({','.join(map(lambda x: f"'{x}'", pull))})
                    group by object_id, date 
                    having count(distinct wnm.{key["column"]}) = {len(pull)}
                    )
                    except (
                    select date, object_id from works_names_mv wnm
                    where wnm.{key["column"]} not in ({','.join(map(lambda x: f"'{x}'", pull))})
                    group by object_id, date)
                )
                select true as is_work, wsv.* from works_names_mv wsv
                join date_cte on date_cte.object_id = wsv.object_id
                and wsv.date = date_cte.date
                where wsv.object_id = date_cte.object_id
                union
                select false as is_work, rnm.* from resources_names_mv rnm
                join date_cte on date_cte.object_id = rnm.object_id
                and rnm.date = date_cte.date
                where rnm.object_id = date_cte.object_id"""

            if len(resource_list) != 0:
                query += f""" and rnm.{res_key["column"]} in ({",".join(map(lambda x: f"'{x}'", resource_list))})"""
            try:
                df = self.execute_query(query)
            except ValueError:
                print("jsondecodeerror occurred", pull)
                yield None
                continue

            if df.empty:
                yield df
                continue

            df["full_fraction"] = df["physical_volume"]

            yield self.convert_df(df)

    def _get_objects_by_resource(self, key):
        if len(self.resource_list) == 0:
            return []

        query = f"""
        select object_id as id from resources_names_mv
        where {key['column']} in ({",".join(map(lambda x: f"'{x}'", self.resource_list))})
        """

        df = self.execute_query(query)
        return df["id"].values.tolist()

    def _get_objects_by_works(self, key):
        query = f"""
            select DISTINCT object_id as id from works_names_mv
            where {key["column"]} in ({",".join(map(lambda x: f"'{x}'", self.works_list))})
            """

        df = self.execute_query(query)
        return df["id"].values.tolist()

    def _select_works_from_db(self):

        query = f"""
        select true as is_work, * from works_names_mv wsv
        where wsv.object_id in ({",".join(map(str, self.object_slice))})
        and wsv.date >= '{self.start_date}'
        """
        if self.ceil_limit != -1:
            query += f"limit {self.ceil_limit}"

        df = self.execute_query(query)
        return df

    def _select_resources_from_db(self, start_date, finish_date):
        query = f"""select false as "is_work", * from resources_names_mv rnm 
                where rnm.object_id in ({",".join(map(str, self.objects))})
                and "date" >= '{start_date.strftime("%Y.%m.%d")}'
                and "date" < '{finish_date.strftime("%Y.%m.%d")}'
            """

        df = self.execute_query(query)
        return df

    def execute_query(self, stmt) -> pd.DataFrame:
        stmt = sql_text(stmt)
        with self.connect.begin() as context:
            df = pd.read_sql(stmt, context)
        return df

    def convert_df(self, df: pd.DataFrame) -> pd.DataFrame:
        col_names = list(df.columns)
        col_names.remove("date")
        col_names.remove("fraction")
        df.loc[:, ["start_date", "finish_date"]].fillna('1970-01-01', inplace=True)
        result = df.pivot_table("fraction", col_names, "date")
        return result.reset_index()

    def __iter__(self):
        self.objects_limit = self.objects_limit if self.objects_limit != -1 else len(self.objects)
        self.index = 0
        self.start_date = "1970-1-1"
        self.object_slice = self.objects[self.index:self.index + self.objects_limit]

        return self

    def __next__(self):
        if len(self.object_slice) == 0:
            raise StopIteration

        try:
            works_df = self._select_works_from_db()
            if len(works_df) == self.ceil_limit:

                self.start_date = works_df.date.max()
                works_df = works_df[works_df.date != self.start_date]
                res_df = self._select_resources_from_db(works_df["date"].min(), works_df["date"].max())
            elif self.ceil_limit == -1 or len(works_df) != self.ceil_limit:
                res_df = self._select_resources_from_db(works_df["date"].min(), works_df["date"].max())
                self.start_date = "1970-1-1"
                self.index += self.objects_limit
                self.object_slice = self.objects[self.index:self.index + self.objects_limit]
            else:
                res_df = pd.DataFrame()

            df = pd.concat([works_df, res_df])

            df = self.convert_df(df)
        except IndexError:
            raise StopIteration
        return df
