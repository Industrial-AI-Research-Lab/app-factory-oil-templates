from abc import abstractmethod

from sqlalchemy import Column, Float, Integer, String, JSON, Identity, LargeBinary
from sqlalchemy.ext.declarative import declarative_base

base = declarative_base()


class BaseModel(base):
    __abstract__ = True

    @property
    @abstractmethod
    def __tablename__(self) -> str:
        pass

    @classmethod
    def get_key(cls, key: str):
        return f'{cls.__tablename__}.{key}'

    @classmethod
    def get_key_id(cls):
        return cls.get_key("id")

    id = Column(Integer, Identity(start=1), primary_key=True)

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in self.__table__.c}


class ResModel(BaseModel):
    __tablename__ = "res_model"

    model_type = Column(String, nullable=False, default='default', index=True)
    name = Column(String, nullable=False, index=True)
    data = Column(LargeBinary, nullable=False)
    measurement_type = Column(String, nullable=True, index=True)
    category = Column(String, nullable=True, index=True)


class TimeModel(BaseModel):
    __tablename__ = "time_model"

    model_type = Column(String, nullable=False, default='default', index=True)
    name = Column(String, nullable=False, index=True)
    data = Column(LargeBinary, nullable=False)
    measurement_type = Column(String, nullable=True, index=True)
    category = Column(String, nullable=True, index=True)


class CachedStatistic(base):
    __tablename__ = "statistics"

    work_start = Column(String, nullable=False, index=True, primary_key=True)
    work_finish = Column(String, nullable=False, index=True, primary_key=True)
    edge_type = Column(String)
    statistic = Column(Float)
    lag = Column(Float)


class HistoricalData(BaseModel):
    __tablename__ = "sampo_historical_data"

    work_id = Column(Integer, nullable=False)
    work_name = Column(String)
    granular_name = Column(String)
    first_day = Column(String)
    last_day = Column(String)
    upper_works = Column(String)
