import os.path
import pickle
from abc import ABC, abstractmethod

from contextlib import contextmanager
from typing import Type
import hashlib

from sqlalchemy import create_engine
from sqlalchemy import select, delete, text as sql_text
from sqlalchemy.exc import ResourceClosedError
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker

from ..adapters.models import BaseModel, ResModel, TimeModel


class ModelsAdapterInterface(ABC):
    def __init__(self, url: str, init_cache_data: list[tuple[str, str, str, str]], echo: bool = False,
                 lazy_cache: bool = True):
        self.cached_quartets = init_cache_data
        self.lazy_cache = lazy_cache
        # temporary dicts for caching models
        self.perf_models = {}
        self.res_models = {}

        self.models_dict = {
            ResModel: self.res_models,
            TimeModel: self.perf_models
        }

    @abstractmethod
    def get_all_models_names(self) -> dict[str, list[str]]:
        """
        Return all models names from resources and performance models
        :rtype: dict[str, list[str]]
        :return: model names as a dictionary:
        {
            "perf_model": list[str],

            "res_model": list[str]
        }

        """

    def get_res_model(self, name: str, category: str, model_type: str | None = None, measurement_type: str | None = None,) -> dict | None:
        """
        Return a resource model from database
        :param model_type: type of model
        :type model_type: str
        :param name: name of resource model
        :type name: str
        :param measurement_type: measurement type of model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: model dict as
        {
            (model_name, measurement_type): model
        } or None if model with name does not exist
        """

        return self._get_model(ResModel, name, model_type, measurement_type, category)

    def get_perf_model(self, name, category: str, model_type: str | None = None, measurement_type=None) -> dict | None:
        """
        Return a performance model from database
        :param model_type: type of the model
        :param name: name of the model
        :type name: str
        :param measurement_type: measurement type of the model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: model dict as
        {
            (model_name, measurement_type): model
        } or None if model with name does not exist
        """
        return self._get_model(TimeModel, name, model_type, measurement_type, category)

    @abstractmethod
    def save_res_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str) -> None:
        """
        Save resource model to database
        :param model_type: type of model
        :type model_type: str
        :param name: name of the model
        :type name: str
        :param model: model for saving
        :type model: dict
        :param category: category of the model
        :type category: str
        :param measurement_type: Optional. Type of measurement for this model
        :type measurement_type: str | None
        :return: None
        """
        ...

    @abstractmethod
    def save_perf_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str):
        """
        Save performance model to database
        :param model_type: type of the model
        :param name: name of the model
        :type name: str
        :param model: model for saving
        :type model: dict
        :param category: category of the model
        :type category: str
        :param measurement_type: Optional. Type of measurement for this model
        :type measurement_type: str | None
        :return: None
        """
        ...

    def _get_model(
            self,
            cls: Type[ResModel] | Type[TimeModel],
            name: str,
            category: str,
            model_type: str | None = None,
            measurement_type: str | None = None
    ):
        ...

    def __load_cache(self):
        for quartet in self.cached_quartets:
            self.models_dict[ResModel][quartet] = self._get_model(
                ResModel,
                name=quartet[0],
                measurement_type=quartet[1],
                model_type=quartet[2],
                category=quartet[3],
            )

            self.models_dict[TimeModel][quartet] = self._get_model(
                TimeModel,
                name=quartet[0],
                measurement_type=quartet[1],
                model_type=quartet[2],
                category=quartet[3],
            )


class FilesModelsAdapter(ModelsAdapterInterface):

    def __init__(self, url: str, init_cache_data: list[tuple[str, str, str, str]] = None, echo: bool = False,
                 lazy_cache: bool = True):
        super().__init__(url, init_cache_data, echo, lazy_cache)
        self.path_to_data = url

    def get_all_models_names(self) -> dict[str, list[str]]:
        """
        Return all models names from resources and performance models
        :rtype: dict[str, list[str]]
        :return: model names as a dictionary:
        {
            "perf_model": list[str],

            "res_model": list[str]
        }

        """
        return {
            'perf_model': os.listdir(os.path.join(self.path_to_data, 'perf_model')),
            'res_model': os.listdir(os.path.join(self.path_to_data, 'res_model'))
        }

    def save_res_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str):
        if not os.path.exists(os.path.join(self.path_to_data, 'res_model')):
            os.makedirs(os.path.join(self.path_to_data, 'res_model'))
        path = os.path.join(self.path_to_data, 'res_model', self.__get_filename(name, measurement_type, model_type, category))

        with open(path, 'wb') as f:
            pickle.dump({
                'name': name,
                'data': model,
                'model_type': model_type,
                'measurement_type': measurement_type,
                'category': category,
            }, f)

    def save_perf_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str):
        if not os.path.exists(os.path.join(self.path_to_data, 'perf_model')):
            os.makedirs(os.path.join(self.path_to_data, 'perf_model'))
        path = os.path.join(self.path_to_data, 'perf_model', self.__get_filename(name, measurement_type, model_type, category))
        with open(path, 'wb') as f:
            pickle.dump({
                'name': name,
                'data': model,
                'model_type': model_type,
                'measurement_type': measurement_type,
                'category': category,
            }, f)

    def _get_model(
            self,
            cls: Type[ResModel] | Type[TimeModel],
            name: str,
            category: str,
            model_type: str | None = None,
            measurement_type: str | None = None
    ):
        model = self.models_dict[cls].get((name, measurement_type, model_type, category))
        if model and self.lazy_cache:
            return model

        path = os.path.join(self.path_to_data, cls.__tablename__,
                            self.__get_filename(name, measurement_type, model_type, category))
        with open(path, 'rb') as f:
            model = pickle.load(f)['data']

        if self.lazy_cache:
            self.models_dict[cls][(name, measurement_type, model_type, category)] = model
        return model

    @staticmethod
    def __get_filename(name: str, measurement_type: str, model_type: str, category: str):
        byte_str = f"{name}_{measurement_type}_{model_type}_{category}".encode('utf-8')
        return f'{int(hashlib.sha1(byte_str).hexdigest(), 16)}.pk'



class ModelsAdapter(ModelsAdapterInterface):
    """
    This class provide access to resources and performance models.
    """

    def __init__(self, url: str, init_cache_data: list[tuple[str, str, str, str]] = None,
                 echo: bool = False, lazy_cache: bool = True):
        """
        Initialize model adapter
        :param url:
        :param init_cache_data: work_name, work_measurement_type, model_type - quartet for getting cache models
        :param echo:
        :param lazy_cache: if true - cache enabled
        """

        super().__init__(url, init_cache_data, echo, lazy_cache)
        self.engine = create_engine(url, echo=echo)
        self.session_factory = sessionmaker(bind=self.engine)


    def get_all_models_names(self) -> dict[str, list[str]]:
        """
        Return all models names from resources and performance models
        :rtype: dict[str, list[str]]
        :return: model names as a dictionary:
        {
            "perf_model": list[str],

            "res_model": list[str]
        }

        """
        return {
            TimeModel.__tablename__: [k['name'] for k in self.__execute_query(select(TimeModel))],
            ResModel.__tablename__: [k['name'] for k in self.__execute_query(select(ResModel))]
        }

    def get_all_models(self) -> dict[str, list[str]]:
        """
        Return all models names from resources and performance models
        :rtype: dict[str, list[str]]
        :return: model names as a dictionary:
        {
            "perf_model": list[str],

            "res_model": list[str]
        }

        """
        return {
            TimeModel.__tablename__: [k for k in self.__execute_query(select(TimeModel))],
            ResModel.__tablename__: [k for k in self.__execute_query(select(ResModel))]
        }


    def save_res_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str) -> None:
        """
        Save resource model to database
        :param model_type: type of model
        :type model_type: str
        :param name: name of the model
        :type name: str
        :param model: model for saving
        :type model: dict
        :param measurement_type: Optional. Type of measurement for this model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: None
        """

        # check exist model
        current = self.get_res_model(name, measurement_type, model_type, category)
        if current is not None:
            # delete model if exist
            self.__execute_query(delete(
                ResModel
            ).where(
                ResModel.name == name,
                ResModel.model_type == model_type,
                ResModel.measurement_type == measurement_type,
                ResModel.category == category,
            ),
                True)
        # upload new model as json
        self.__save_to_database([
            ResModel(
                name=name,
                model_type=model_type,
                data=model,
                measurement_type=measurement_type,
                category=category,
            )],
        )

    def save_perf_model(self, name: str, model_type: str, measurement_type: str, model: bytes, category: str) -> None:
        """
        Save performance model to database
        :param model_type: type of the model
        :param name: name of the model
        :type name: str
        :param model: model for saving
        :type model: dict
        :param measurement_type: Optional. Type of measurement for this model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: None
        """

        current = self.get_perf_model(name, measurement_type)
        if current is not None:
            self.__execute_query(delete(
                TimeModel
            ).where(
                TimeModel.name == name,
                TimeModel.model_type == model_type,
                TimeModel.measurement_type == measurement_type,
                TimeModel.category == category,
            ), True)

        self.__save_to_database([TimeModel(
            name=name,
            model_type=model_type,
            data=model,
            measurement_type=measurement_type,
            category=category,
        )])

    def remove_all_models(self):
        self.__execute_query(delete(
            TimeModel
        ), commit=True)

        self.__execute_query(delete(
            ResModel
        ), commit=True)

    def get_res_model(self, name: str, category: str, model_type: str | None = None, measurement_type: str | None = None,) -> dict | None:
        """
        Return a resource model from database
        :param model_type: type of model
        :type model_type: str
        :param name: name of resource model
        :type name: str
        :param measurement_type: measurement type of model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: model dict as
        {
            (model_name, measurement_type): model
        } or None if model with name does not exist
        """

        return self._get_model(
            cls=ResModel,
            name=name,
            category=category,
            model_type=model_type,
            measurement_type=measurement_type
        )

    def get_perf_model(self, name, category: str, model_type: str | None = None, measurement_type=None) -> dict | None:
        """
        Return a performance model from database
        :param model_type: type of the model
        :param name: name of the model
        :type name: str
        :param measurement_type: measurement type of the model
        :type measurement_type: str | None
        :param category: category of model
        :type category: str
        :return: model dict as
        {
            (model_name, measurement_type): model
        } or None if model with name does not exist
        """
        return self._get_model(
            cls=TimeModel,
            name=name,
            category=category,
            model_type=model_type,
            measurement_type=measurement_type
        )

    def _get_model(
            self,
            cls: Type[ResModel] | Type[TimeModel],
            name: str,
            category: str,
            model_type: str | None = None,
            measurement_type: str | None = None
    ):
        if model_type and measurement_type is None:
            return self.__execute_query(
                select(cls).where(cls.name == name)
                .where(cls.model_type == model_type)
            )
        elif measurement_type and model_type is None:
            return self.__execute_query(
                select(cls).where(cls.name == name)
                .where(cls.measurement_type == measurement_type)
            )
        elif measurement_type is None and model_type is None:
            return self.__execute_query(
                select(cls).where(cls.name == name)
            )
        else:
            if self.lazy_cache:
                model = self.models_dict[cls].get((name, measurement_type, model_type, category))
                if model is None:
                    model = self.__execute_query(
                        select(cls).where(cls.name == name)
                        .where(cls.model_type == model_type)
                        .where(cls.measurement_type == measurement_type)
                        .where(cls.category == category)
                    )
                    self.models_dict[cls][(name, measurement_type, model_type, category)] = model
                return model

            return self.__execute_query(
                        select(cls).where(cls.name == name)
                        .where(cls.model_type == model_type)
                        .where(cls.measurement_type == measurement_type)
                        .where(cls.category == category)
                    )

    def __get_last_id_from_db(self, cls: Type[BaseModel]):
        query = sql_text(f"select max(id) from {cls.__tablename__}")
        last_id = self.__execute_query(query)
        last_id = last_id[0]
        if last_id is None:
            return 0
        return last_id

    def __get_basic_objects(self, cls):
        query = select(cls)
        result = self.__execute_query(query)
        return {(o.name, o.measurement): o for o in result}

    def __save(self, collections: list[list]):
        for collection in collections:
            self.__save_to_database(collection)

    def __save_to_database(self, collection):
        with self.__get_session() as context:
            context.add_all(collection)
            context.commit()

    def __get_last_id(self, collection: list, cls: Type[BaseModel]):
        if len(collection):
            return max([o.id for o in collection])
        else:
            last_id = self.__get_last_id_from_db(cls)
            return 0 if last_id is None else last_id

    def __execute_query(self, query, commit=False) -> list:
        with self.__get_session() as context:
            result = context.execute(query)
            try:
                result = [o.to_dict() for o in result.scalars().all()]
            except ResourceClosedError:
                pass
            if commit:
                context.commit()
        return result

    @contextmanager
    def __get_session(self):
        session = scoped_session(self.session_factory)
        try:
            yield session
        finally:
            session.close()
