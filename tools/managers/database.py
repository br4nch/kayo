from json import dumps, loads
from typing import Any, Optional

import asyncpg
from config import Database
from tools.managers import logging

log = logging.getLogger(__name__)

class Record(asyncpg.Record):
    def __getattr__(self, attr: str):
        return self.get(attr)

async def setup(pool: asyncpg.Pool) -> asyncpg.Pool:
    with open("schema.sql", "r", encoding="UTF-8") as buffer:
        schema = buffer.read()
        await pool.execute(schema)

    return pool

async def connect(**kwargs):
    kwargs['record_class'] = Record 
    
    pool = await asyncpg.create_pool(**kwargs)

    if not pool:
        raise Exception("Could not establish a connection to postgresql")
    
    return await setup(pool)
