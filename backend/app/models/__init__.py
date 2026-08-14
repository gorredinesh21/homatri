"""Import all model modules so Base.metadata knows every table."""

from backend.app.models import admin, chef, customer, driver, shared, system  # noqa: F401
