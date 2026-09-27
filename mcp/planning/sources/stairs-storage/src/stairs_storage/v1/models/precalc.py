from typing import TypeVar, Type, ClassVar, Any
from sqlalchemy.engine import Connection
from sqlalchemy import text

from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum

from sqlalchemy.dialects.postgresql import psycopg2

T = TypeVar("T", bound="BasePrecalcModel")


class PrecalcModelsTypeEnum(Enum):
    TASK = 'task'
    BWD = 'bdw'
    OSE = 'ose'



class BasePrecalcModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    def __hash__(self) -> int:
        ...

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return False

        return hash(self) == hash(other)

    @classmethod
    def get_table_name(cls) -> str:
        raise NotImplementedError

    @classmethod
    def get_from_database(
        cls: Type[T],
        conn: Connection,
        **filters
    ) -> list[T]:

        table = cls.get_table_name()

        conditions = []
        params = {}

        for i, (key, value) in enumerate(filters.items()):
            if value is not None:
                param = f"p{i}"
                conditions.append(f"{key} = :{param}")
                params[param] = str(value)

        where = " AND ".join(conditions) if conditions else "TRUE"

        query = text(f"""
            SELECT * FROM {table}
            WHERE {where}
        """)
        result = conn.execute(query, params)
        rows = result.mappings().all()

        return [cls(**row) for row in rows]

    def save_to_database(self, conn: Connection):
        table = self.get_table_name()
        data = self.model_dump(mode="json")

        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())

        query = text(f"""
            INSERT INTO {table} ({columns})
            VALUES ({placeholders})
        """)

        conn.execute(query, data)

    # -------- DELETE --------
    @classmethod
    def delete_from_database(
        cls,
        conn: Connection,
        **filters
    ) -> int:

        if not filters:
            raise ValueError("DELETE without filters is forbidden")

        table = cls.get_table_name()

        conditions = []
        params = {}

        for i, (key, value) in enumerate(filters.items()):
            if value is not None:
                param = f"p{i}"
                conditions.append(f"{key} = :{param}")
                params[param] = str(value)

        where = " AND ".join(conditions) if conditions else "TRUE"

        query = text(f"""
            DELETE FROM {table}
            WHERE {where}
        """)

        result = conn.execute(query, params)
        conn.commit()
        return result.rowcount

    def get_key(self):
        ...


class TaskPrecalcModel(BasePrecalcModel):
    """
    Precalc for works
    """

    precalc_type: ClassVar[PrecalcModelsTypeEnum] = Field(PrecalcModelsTypeEnum.TASK)

    occ_code: str | int = Field(...) # key
    ose_code: str | int = Field(...) # key
    bwd: str = Field(...) # key
    task_name: str = Field(...)
    task_category: str = Field(...)
    task_measurement: str = Field(...)
    task_volume: float = Field(...)

    def __hash__(self) -> int:
        return hash((
            self.occ_code, self.ose_code, self.bwd, self.task_name,
            self.task_measurement, self.task_volume
        ))

    @classmethod
    def get_table_name(cls) -> str:
        return "precalc_tasks"

    def get_key(self):
        return self.occ_code, self.ose_code, self.bwd

class BWDPrecalcModel(BasePrecalcModel):
    """
    Precalc for brand of the working documentation (Марка РД - марка рабочей документации)
    """

    precalc_type: ClassVar[PrecalcModelsTypeEnum] = Field(PrecalcModelsTypeEnum.BWD)

    occ_code: str | int = Field(...) # key
    ose_code: str | int = Field(...) # key
    bwd: str = Field(...)

    def __hash__(self) -> int:
        return hash((
            self.occ_code, self.ose_code, self.bwd
        ))

    @classmethod
    def get_table_name(cls) -> str:
        return "precalc_bwd"

    def get_key(self):
        return self.occ_code, self.ose_code


class OSEPrecalcModel(BasePrecalcModel):
    """
    Precalc for object of summary estimates OSE (ОССР - объект сводно-сметного расчета)
    """

    precalc_type: ClassVar[PrecalcModelsTypeEnum] = PrecalcModelsTypeEnum.OSE

    occ_code: str | int = Field(...)
    ose_list: tuple[str | int, ...] = Field(...)

    def __hash__(self) -> int:
        return hash((self.occ_code, self.ose_list))

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, OSEPrecalcModel):
            return NotImplemented
        return self.occ_code == other.occ_code

    @classmethod
    def get_table_name(cls) -> str:
        return "precalc_ose"

    @field_validator("ose_list", mode="before")
    @classmethod
    def convert_ose_list_to_tuple(cls, v):
        if isinstance(v, list):
            return tuple(v)
        return v

    def get_key(self):
        return self.occ_code

class PrecalcRequestModel(BasePrecalcModel):
    occ_code: str | int = Field(...)
    ose_code: str | int | None = Field(...)
    bwd: str | None = Field(...)
