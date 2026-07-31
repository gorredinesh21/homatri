"""Import all model modules so Base.metadata knows every table."""

from app.models import admin, chef, customer, driver, shared, system  # noqa: F401
