import os
import sys

# The app package moved to backend/app and imports itself as `backend.app.*`,
# while the tests still import `app.*`. Alias the whole subtree so SQLAlchemy
# metadata registers exactly once.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from importlib.abc import MetaPathFinder  # noqa: E402
from importlib.util import find_spec as _find_spec


class _AppAlias(MetaPathFinder):
    """Redirect `app.*` <-> `backend.app.*` so each module executes exactly once."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app" or fullname.startswith("app."):
            real = "backend.app" + fullname[3:]
        else:
            return None
        if real in sys.modules:
            return _stub_spec(fullname, sys.modules[real])
        try:
            spec = _find_spec(real)
        except (ImportError, ValueError):
            return None
        if spec is None:
            return None
        import importlib.util

        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(fullname, None)
            raise
        sys.modules[real] = module
        return _stub_spec(fullname, module)


def _stub_spec(fullname, module):
    import importlib.util

    spec = importlib.util.spec_from_loader(fullname, loader=None)
    spec.loader = _ExecOnceLoader(module)
    return spec


class _ExecOnceLoader:
    def __init__(self, module):
        self.module = module

    def create_module(self, spec):
        return self.module

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _AppAlias())

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402

@pytest.fixture(scope="function")
async def test_engine():
    """Create a function-scoped async engine with NullPool to avoid event loop mismatch."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture(scope="function")
async def db_session(test_engine):
    """Provide a clean AsyncSession bound to the test engine."""
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
