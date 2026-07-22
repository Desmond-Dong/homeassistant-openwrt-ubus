"""Client for the OpenWrt ubus API."""

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_DEF_DEBUG,
    API_DEF_SESSION_ID,
    API_DEF_TIMEOUT,
    API_DEF_VERIFY,
    API_ERROR,
    API_MESSAGE,
    API_METHOD_LOGIN,
    API_METHOD_DESTROY,
    API_PARAM_PASSWORD,
    API_PARAM_USERNAME,
    API_RESULT,
    API_RPC_CALL,
    API_RPC_VERSION,
    API_SUBSYS_SESSION,
    API_UBUS_RPC_SESSION,
    API_UBUS_RPC_SESSION_EXPIRES,
    API_SESSION_METHOD_LIST,
    HTTP_STATUS_OK,
    UBUS_ERROR_SUCCESS,
    UBUS_ERROR_PERMISSION_DENIED,
    UBUS_ERROR_NOT_FOUND,
    UBUS_ERROR_NO_DATA,
    _get_error_message,
)

_LOGGER = logging.getLogger(__name__)


class PreparedCall:
    def __init__(
        self,
        rpc_method: str,
        subsystem: str | None = None,
        method: str | None = None,
        params: dict | None = None,
        rpc_id: str | None = None,
    ):
        self.rpc_method = rpc_method
        self.subsystem = subsystem
        self.method = method
        self.params = params
        self.id = rpc_id


class RPCError(RuntimeError):
    """Custom exception for RPC errors."""
    pass


class Ubus:
    """Interacts with the OpenWrt ubus API."""

    def __init__(
        self,
        url,
        hostname: str,
        username,
        password,
        session: aiohttp.ClientSession,
        timeout,
        verify,
    ):
        self.url = url
        self.hostname = hostname
        self.username = username
        self.password = password
        self.session = session
        self.timeout = timeout
        self.verify = verify

        self.debug_api = API_DEF_DEBUG
        self.session_id: str | None = None
        self.session_expire = 0
        self._session_created_internally = False
        self._connect_lock = asyncio.Lock()

    def set_session(self, session):
        self.session = session

    async def logout(self):
        try:
            await self._api_call(API_RPC_CALL, API_SUBSYS_SESSION, API_METHOD_DESTROY)
        finally:
            self.session_id = None
            self.session_expire = 0

    def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self._session_created_internally = True

    async def _ensure_session_is_valid(self):
        if self.session_expire <= (time.time() - 15):
            async with self._connect_lock:
                if self.session_expire <= (time.time() - 15):
                    await self.connect()

    async def api_call(self, rpc_method, subsystem=None, method=None, params=None) -> dict | list | None:
        await self._ensure_session_is_valid()
        return await self._api_call(rpc_method, subsystem, method, params)

    async def batch_call(self, rpcs: list[PreparedCall]) -> list[tuple[str, dict | list | None | Exception]] | None:
        await self._ensure_session_is_valid()
        return await self._batch_call(rpcs)

    async def _batch_call(self, rpcs: list[PreparedCall]) -> list[tuple[str, dict | list | None | Exception]] | None:
        self._ensure_session()

        if rpcs[0] and rpcs[0].subsystem != API_SUBSYS_SESSION:
            rpcs.append(PreparedCall(rpc_method=API_RPC_CALL, subsystem=API_SUBSYS_SESSION, method=API_SESSION_METHOD_LIST, rpc_id="refresh_expiration"))

        rpc_calls = []
        for rpc in rpcs:
            params: list[Any] = [self.session_id or API_DEF_SESSION_ID, rpc.subsystem]
            if rpc.rpc_method == API_RPC_CALL:
                if rpc.method:
                    params.append(rpc.method)
                params.append(rpc.params if rpc.params else {})
            rpc_call = {"jsonrpc": API_RPC_VERSION, "method": rpc.rpc_method, "params": params}
            if rpc.id is not None:
                rpc_call["id"] = rpc.id
            rpc_calls.append(rpc_call)

        response = None
        retries_left = 5
        while retries_left > 0:
            try:
                response = await self.session.post(
                    url=self.url, server_hostname=self.hostname,
                    data=json.dumps(rpc_calls), timeout=self.timeout, verify_ssl=self.verify,
                )
                break
            except aiohttp.ClientConnectionError as e:
                _LOGGER.warning("Connection error when calling API: %s", e)
                retries_left -= 1
                if retries_left == 0:
                    raise ConnectionError(f"Failed to connect to API after multiple attempts: {e}")
                _LOGGER.debug("Retrying API call... (%d retries left)", retries_left)
                await asyncio.sleep(5 - retries_left)
            except Exception as e:
                _LOGGER.error("Unexpected error when calling API: %s", e)
                raise ConnectionError(f"Unexpected error when calling API: {e}")

        if response.status != HTTP_STATUS_OK:
            return None

        responses = await response.json()

        if isinstance(responses, list):
            results: list[tuple[str, dict | list | None | Exception]] = []
            for i, response in enumerate(responses):
                result_id = response.get("id", "")

                def _append_result(_result):
                    results.append((result_id, _result))

                if API_ERROR in response:
                    subsystem = rpcs[i].subsystem
                    method = rpcs[i].method
                    error_message = response[API_ERROR].get(API_MESSAGE, "Unknown error")
                    error_code = response[API_ERROR].get("code", -1)

                    if error_code == -32002 or "Access denied" in error_message:
                        _LOGGER.warning("Permission denied when calling %s.%s: %s [session_id: %s]", subsystem, method, error_message, self.session_id)
                        _append_result(PermissionError(f"Permission denied for {subsystem}.{method}: {error_message} (code: {error_code})"))
                    else:
                        _LOGGER.error("API call failed for %s.%s: %s (code: %d) [session_id: %s]", subsystem, method, error_message, error_code, self.session_id)
                        _append_result(ConnectionError(f"API call failed for {subsystem}.{method}: {error_message} (code: {error_code})"))
                else:
                    result = response[API_RESULT]
                    if rpcs[i].rpc_method == API_RPC_CALL:
                        if isinstance(result, list):
                            error_code = result[0]
                            error_msg = _get_error_message(error_code)
                            if len(result) == 2:
                                if error_code == UBUS_ERROR_SUCCESS:
                                    _append_result(result[1])
                                else:
                                    _append_result(RPCError(f"API call failed with error code {error_code} ({error_msg}): {result[1]}"))
                            elif len(result) == 1:
                                if error_code == UBUS_ERROR_SUCCESS:
                                    _append_result(None)
                                else:
                                    _append_result(RPCError(f"API call failed with error code {error_code} ({error_msg}): No error message"))
                            else:
                                _append_result(ConnectionError(f"Unexpected API call result format: {result}"))
                        else:
                            _append_result(ConnectionError(f"Unexpected API call result format: {result}"))
                    else:
                        _append_result(result)
            if results[-1][0] == "refresh_expiration":
                session_response = results.pop()[1]
                if isinstance(session_response, Exception):
                    try:
                        raise session_response
                    except (RPCError, PermissionError) as e:
                        _LOGGER.warning("Failed to retrieve session expiration: %s [session_id: %s]", e, self.session_id)
                elif isinstance(session_response, dict):
                    self.session_expire = time.time() + session_response.get("expires", 0)

            return results
        else:
            raise ConnectionError(f"Unexpected API response format: {responses}")

    async def _api_call(self, rpc_method, subsystem=None, method=None, params=None) -> dict | list | None:
        results = await self._batch_call([PreparedCall(rpc_method=rpc_method, subsystem=subsystem, method=method, params=params)])
        if results is None:
            return None
        _, response = results[0]
        if isinstance(response, Exception):
            raise response
        return response

    def api_debugging(self, debug_api):
        self.debug_api = debug_api
        return self.debug_api

    def https_verify(self, verify):
        self.verify = verify
        return self.verify

    async def connect(self):
        if self.session_id is not None:
            try:
                await self._api_call(API_RPC_CALL, API_SUBSYS_SESSION, API_METHOD_DESTROY)
            except Exception:
                pass

        self.session_expire = 0
        self.session_id = None

        login = await self._api_call(API_RPC_CALL, API_SUBSYS_SESSION, API_METHOD_LOGIN, {
            API_PARAM_USERNAME: self.username,
            API_PARAM_PASSWORD: self.password,
        })
        if login and API_UBUS_RPC_SESSION in login:
            self.session_id = login[API_UBUS_RPC_SESSION]
            self.session_expire = time.time() + int(login.get(API_UBUS_RPC_SESSION_EXPIRES, 300))
        else:
            self.session_id = None

        return self.session_id

    async def close(self):
        try:
            await self.logout()
        except Exception as exc:
            _LOGGER.debug("Error destroying ubus session during close: %s", exc)

        if self.session and not self.session.closed and self._session_created_internally:
            await self.session.close()
            self.session = None
            self._session_created_internally = False