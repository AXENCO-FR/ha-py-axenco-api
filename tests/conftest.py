import pytest
import aiohttp
from pyaxencoapi.api import PyAxencoAPI

@pytest.fixture(scope="session")
def event_loop():
    """Use a session-scoped event loop for async tests."""
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def api_client():
    """Fixture for an initialized PyAxencoAPI client."""
    async with aiohttp.ClientSession() as session:
        client = PyAxencoAPI(
            email="test@example.com",
            password="securepassword",
            session=session
        )
        yield client
