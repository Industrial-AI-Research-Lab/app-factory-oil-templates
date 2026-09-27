import ast
from functools import wraps

import psycopg2
from sqlalchemy import create_engine, Engine

from abc import ABC, abstractmethod

from ..models.precalc import (
    PrecalcModelsTypeEnum,
    TaskPrecalcModel,
    BWDPrecalcModel,
    OSEPrecalcModel,
)

ALIASES = {
    "oks": "occ_code",
    "oks_code": "occ_code",
    "ossr": "ose_code",
    "ossr_code": "ose_code",
    "ossr_list": "ose_list",
    "marka": "bwd",
    "mark": "bwd",
    "task": "task_name"
}

class PrecalcAdapterInterface(ABC):

    def __init__(self, lazy_cache: bool = True):

        self.lazy_cache = lazy_cache

        self.cached_models = {
            precalc_type: {} for precalc_type in PrecalcModelsTypeEnum
        }


    @staticmethod
    def _resolve_model(occ_code, ose_code = None, bwd = None):
        """
        get model by keys
        """
        ose_key = ose_code is not None
        bwd_key = bwd is not None

        if ose_key and bwd_key:
            return TaskPrecalcModel, PrecalcModelsTypeEnum.TASK

        if ose_key and not bwd_key:
            return BWDPrecalcModel, PrecalcModelsTypeEnum.BWD

        if not ose_key and not bwd_key:
            return OSEPrecalcModel, PrecalcModelsTypeEnum.OSE

        raise Exception("Model type not found")

    @staticmethod
    def _validate_model(func):
        def wrapper(self, data: list[dict]):

            valid_data = [{self._keys_mapper(k): v for k, v in d.items()} for d in data]
            return func(self, valid_data)
        return wrapper

    @staticmethod
    def _cache(ptype: PrecalcModelsTypeEnum):
        def decorator(func):
            def wrapper(self, **kwargs):
                key = tuple(kwargs.values())
                if key in self.cached_models[ptype]:
                    result = self.cached_models[ptype][key]
                    if result:
                        return result

                result = func(self, **kwargs)

                if self.lazy_cache:
                    for model in result:
                        if model.get_key() in self.cached_models[ptype]:
                            self.cached_models[ptype][model.get_key()].add(model)
                        else:
                            self.cached_models[ptype][model.get_key()] = set(model, )

                return result
            return wrapper
        return decorator


class PrecalcAdapter(PrecalcAdapterInterface):

    def __init__(self, url: str, lazy_cache: bool = True):
        super().__init__(lazy_cache)

        self.engine: Engine = create_engine(
            url
        )

    @PrecalcAdapterInterface._validate_model
    def save_task(self, models: list[dict]):
        with self.engine.begin() as conn:
            for model in models:
                task_model = TaskPrecalcModel(**model)
                task_model.save_to_database(conn)

            conn.commit()

    @PrecalcAdapterInterface._validate_model
    def save_bwd(self, models: list[dict]):
        with self.engine.begin() as conn:
            for model in models:
                bwd_model = BWDPrecalcModel(**model)
                bwd_model.save_to_database(conn)

            conn.commit()

    @PrecalcAdapterInterface._validate_model
    def save_ose(self, models: list[dict]):

        with self.engine.begin() as conn:
            for model in models:
                model['ose_list'] = tuple(ast.literal_eval(model['ose_list']))
                ose_model = OSEPrecalcModel(**model)
                ose_model.save_to_database(conn)

            conn.commit()



    @PrecalcAdapterInterface._cache(PrecalcModelsTypeEnum.BWD)
    def get_bwd(self, occ_code=None, ose_code=None):
        if self.lazy_cache:
            key = (occ_code, ose_code)
            if key in self.cached_models[PrecalcModelsTypeEnum.BWD]:
                return self.cached_models[PrecalcModelsTypeEnum.BWD][key]

        with self.engine.begin() as conn:
            result = BWDPrecalcModel.get_from_database(
                conn,
                occ_code=occ_code,
                ose_code=ose_code
            )

        return result

    @PrecalcAdapterInterface._cache(PrecalcModelsTypeEnum.OSE)
    def get_ose(self, occ_code=None):
        if self.lazy_cache:
            if occ_code in self.cached_models[PrecalcModelsTypeEnum.OSE]:
                return self.cached_models[PrecalcModelsTypeEnum.OSE][occ_code]

        with self.engine.begin() as conn:
            result = OSEPrecalcModel.get_from_database(
                conn,
                occ_code=occ_code
            )

        return result

    @PrecalcAdapterInterface._cache(PrecalcModelsTypeEnum.TASK)
    def get_task(self, occ_code=None, ose_code=None, bwd=None):

        if self.lazy_cache:
            key = (occ_code, ose_code, bwd)
            if key in self.cached_models[PrecalcModelsTypeEnum.TASK]:
                return self.cached_models[PrecalcModelsTypeEnum.TASK][key]

        with self.engine.begin() as conn:
            result = TaskPrecalcModel.get_from_database(
                conn,
                occ_code=occ_code,
                ose_code=ose_code,
                bwd=bwd
            )

        return result

    def delete_bwd(self, occ_code=None, ose_code=None):
        with self.engine.begin() as conn:
            BWDPrecalcModel.delete_from_database(conn, occ_code=occ_code, ose_code=ose_code)

        if (occ_code, ose_code) in self.cached_models[PrecalcModelsTypeEnum.BWD]:
            del self.cached_models[PrecalcModelsTypeEnum.BWD][(occ_code, ose_code)]


    def delete_ose(self, occ_code=None):
        if occ_code in self.cached_models[PrecalcModelsTypeEnum.OSE]:
            del self.cached_models[PrecalcModelsTypeEnum.OSE][occ_code]

        with self.engine.begin() as conn:
            OSEPrecalcModel.delete_from_database(conn, occ_code=occ_code)

    def delete_task(self, occ_code=None, ose_code=None, bwd=None):
        key = (occ_code, ose_code, bwd)
        if key in self.cached_models[PrecalcModelsTypeEnum.TASK]:
            del self.cached_models[PrecalcModelsTypeEnum.TASK][key]

        with self.engine.begin() as conn:
            TaskPrecalcModel.delete_from_database(conn, occ_code=occ_code, ose_code=ose_code, bwd=bwd)

    def get_precalc(self, **kwargs):
        pass

    def save_precalc(self, **kwargs):
        pass

    @staticmethod
    def _keys_mapper(row: str):
        return ALIASES.get(row.lower(), row)