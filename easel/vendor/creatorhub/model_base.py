"""A model registry belongs to one Easel operations runtime."""
from sqlalchemy.orm import registry
from sqlmodel import SQLModel as BaseSQLModel


class SQLModel(BaseSQLModel, registry=registry()):
    pass
