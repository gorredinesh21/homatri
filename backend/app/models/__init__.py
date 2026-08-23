"""Import all model modules so Base.metadata knows every table."""

from backend.app.models import admin, auth, catering, chef, customer, driver, shared, social, system  # noqa: F401
