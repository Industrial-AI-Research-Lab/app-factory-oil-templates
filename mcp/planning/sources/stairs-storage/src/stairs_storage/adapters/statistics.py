import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import sessionmaker

from ..adapters.models import HistoricalData, CachedStatistic


class Statistics:
    """
    Class provide access to statistics data
    """

    def __init__(self, url):
        self.engine = create_engine(url)
        self.session_factory = sessionmaker(bind=self.engine)

    def get_history_data(self):
        """
        Return all history data
        :return: dataframe with historical data
        :rtype pandas.DataFrame:
        """

        with self.engine.begin() as context:
            stmt = sql_text(f'select * from {HistoricalData.__tablename__}')
            return pd.read_sql(stmt, context)

    def save_statistic_to_cache(self, statistic: dict):
        with self.session_factory() as context:
            context.merge(CachedStatistic(**statistic))
            context.commit()

    def get_statistic_from_cache(self, works: tuple[str, str]) -> dict:
        with self.session_factory() as context:
            result = dict(**context.scalars(
                select(
                    CachedStatistic
                ).where(
                    CachedStatistic.work_start == works[0],
                    CachedStatistic.work_finish == works[1]
                )
            ).one_or_none().__dict__)
            if '_sa_instance_state' in result:
                del result['_sa_instance_state']
            return result
