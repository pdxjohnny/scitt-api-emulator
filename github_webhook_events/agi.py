import os
import abc
import sys
import pdb
import enum
import uuid
import json
import pprint
import asyncio
import getpass
import pathlib
import argparse
import contextlib
import collections
import dataclasses
from typing import Any, NewType, AsyncIterator

import openai
import keyring


class AGIEventType(enum.Enum):
    NEW_AGENT_CREATED = enum.auto()
    EXISTING_AGENT_RETRIEVED = enum.auto()
    NEW_THREAD_CREATED = enum.auto()
    NEW_THREAD_RUN_CREATED = enum.auto()
    NEW_THREAD_MESSAGE = enum.auto()
    THREAD_RUN_COMPLETE = enum.auto()
    THREAD_RUN_EVENT_WITH_UNKNOWN_STATUS = enum.auto()


@dataclasses.dataclass
class AGIEvent:
    event_type: AGIEventType
    event_data: Any


@dataclasses.dataclass
class AGIEventNewAgent:
    agent_id: str
    agent_name: str


@dataclasses.dataclass
class AGIEventNewThreadCreated:
    agent_id: str
    thread_id: str


@dataclasses.dataclass
class AGIEventNewThreadRunCreated:
    agent_id: str
    thread_id: str
    run_id: str


@dataclasses.dataclass
class AGIEventThreadRunComplete:
    agent_id: str
    thread_id: str
    run_id: str
    status: str


@dataclasses.dataclass
class AGIEventThreadRunEventWithUnknwonStatus(AGIEventNewThreadCreated):
    agent_id: str
    thread_id: str
    run_id: str
    status: str


@dataclasses.dataclass
class AGIEventNewThreadMessage:
    agent_id: str
    thread_id: str
    message_content: str


class AGIActionType(enum.Enum):
    NEW_AGENT = enum.auto()
    INGEST_FILE = enum.auto()
    ADD_MESSAGE = enum.auto()
    NEW_THREAD = enum.auto()


@dataclasses.dataclass
class AGIAction:
    action_type: AGIActionType
    action_data: Any


@dataclasses.dataclass
class AGIActionNewAgent:
    agent_id: str
    agent_name: str
    agent_instructions: str


@dataclasses.dataclass
class AGIActionNewThread:
    agent_id: str


@dataclasses.dataclass
class AGIActionAddMessage:
    thread_id: str
    add_message: str


AGIActionStream = NewType("AGIActionStream", AsyncIterator[AGIAction])


class _KV_STORE_DEFAULT_VALUE:
    pass


KV_STORE_DEFAULT_VALUE = _KV_STORE_DEFAULT_VALUE()


class KVStore(abc.ABC):
    @abc.abstractmethod
    async def get(self, key, default_value: Any = KV_STORE_DEFAULT_VALUE):
        raise NotImplementedError()

    @abc.abstractmethod
    async def set(self, key, value):
        raise NotImplementedError()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_value, _traceback):
        return


class KVStoreKeyring(KVStore):
    def __init__(self, config):
        self.service_name = config["service_name"]

    async def get(self, key, default_value: Any = KV_STORE_DEFAULT_VALUE):
        if default_value is not KV_STORE_DEFAULT_VALUE:
            return self.keyring_get_password_or_return(
                self.service_name,
                key,
                not_found_return_value=default_value,
            )
        return keyring.get_password(self.service_name, key)

    async def set(self, key, value):
        return keyring.set_password(self.service_name, key, value)

    @staticmethod
    def keyring_get_password_or_return(
        service_name: str,
        username: str,
        *,
        not_found_return_value=None,
    ) -> str:
        with contextlib.suppress(Exception):
            value = keyring.get_password(service_name, username)
            if value is not None:
                return value
        return not_found_return_value


def make_argparse_parser(argv=None):
    parser = argparse.ArgumentParser(description="LLM Based Assistant")
    parser.add_argument(
        "--agi-name",
        dest="agi_name",
        default="alice",
        type=str,
    )
    parser.add_argument(
        "--kvstore-service-name",
        dest="kvstore_service_name",
        default="alice",
        type=str,
    )
    parser.add_argument(
        "--openai-api-key",
        dest="openai_api_key",
        type=str,
        default=KVStoreKeyring.keyring_get_password_or_return(
            getpass.getuser(),
            "openai.api.key",
        ),
        help="OpenAI API Key",
    )

    return parser


import inspect
import asyncio
from collections import UserList
from contextlib import AsyncExitStack
from typing import (
    Dict,
    Any,
    AsyncIterator,
    Tuple,
    Type,
    AsyncContextManager,
    Optional,
    Set,
)


async def concurrently(
    work: Dict[asyncio.Task, Any],
    *,
    errors: str = "strict",
    nocancel: Optional[Set[asyncio.Task]] = None,
) -> AsyncIterator[Tuple[Any, Any]]:
    # Track if first run
    first = True
    # Set of tasks we are waiting on
    tasks = set(work.keys())
    # Return when outstanding operations reaches zero
    try:
        while first or tasks:
            first = False
            # Wait for incoming events
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                # Remove the task from the set of tasks we are waiting for
                tasks.remove(task)
                # Get the tasks exception if any
                exception = task.exception()
                if errors == "strict" and exception is not None:
                    raise exception
                if exception is None:
                    # Remove the compeleted task from work
                    complete = work[task]
                    del work[task]
                    yield complete, task.result()
                    # Update tasks in case work has been updated by called
                    tasks = set(work.keys())
    finally:
        for task in tasks:
            if not task.done() and (nocancel is None or task not in nocancel):
                task.cancel()
            else:
                # For tasks which are done but have exceptions which we didn't
                # raise, collect their exceptions
                task.exception()


async def agent_openai(
    tg: asyncio.TaskGroup,
    agi_name: str,
    kvstore: KVStore,
    action_stream: AGIActionStream,
    openai_api_key: str,
    *,
    openai_base_url: Optional[str] = None,
):
    client = openai.AsyncOpenAI(
        api_key=openai_api_key,
        base_url=openai_base_url,
    )

    agents = {}
    threads = {}

    action_stream_iter = action_stream.__aiter__()
    work = {
        tg.create_task(action_stream_iter.__anext__()): (
            "action_stream",
            action_stream_iter,
        ),
    }
    async for (work_name, work_ctx), result in concurrently(work):
        print(f"openai_agent.{work_name}", pprint.pformat(result))
        # TODO There should be no await's here, always add to work
        if work_name == "action_stream":
            work[tg.create_task(work_ctx.__anext__())] = (work_name, work_ctx)
            if result.action_type == AGIActionType.NEW_AGENT:
                assistant = None
                if result.action_data.agent_id:
                    with contextlib.suppress(openai.NotFoundError):
                        assistant = await client.beta.assistants.retrieve(
                            assistant_id=result.action_data.agent_id,
                        )
                        yield AGIEvent(
                            event_type=AGIEventType.EXISTING_AGENT_RETRIEVED,
                            event_data=AGIEventNewAgent(
                                agent_id=assistant.id,
                                agent_name=result.action_data.agent_name,
                            ),
                        )
                if not assistant:
                    assistant = await client.beta.assistants.create(
                        name=result.action_data.agent_name,
                        instructions=result.action_data.agent_instructions,
                        # model="gpt-4-1106-preview",
                        model=await kvstore.get(
                            f"openai.assistants.{agi_name}.model",
                            "gpt-3.5-turbo-1106",
                        ),
                        tools=[{"type": "retrieval"}],
                        # file_ids=[file.id],
                    )
                    yield AGIEvent(
                        event_type=AGIEventType.NEW_AGENT_CREATED,
                        event_data=AGIEventNewAgent(
                            agent_id=assistant.id,
                            agent_name=result.action_data.agent_name,
                        ),
                    )
            if result.action_type == AGIActionType.INGEST_FILE:
                # TODO aiofile and tg.create_task
                with open(result.action_data.file_path, "rb") as fileobj:
                    file = await client.files.create(
                        file=fileobj,
                        purpose="assistants",
                    )
                await agents[result.action_data.agent_id].update(
                    file_ids=agents[result.action_data.agent_id].file_ids
                    + file.id,
                )
            elif result.action_type == AGIActionType.NEW_THREAD:
                run = await client.beta.threads.create_and_run(
                    assistant_id=result.action_data.agent_id,
                )
                threads[run.thread_id] = run.thread_id
                yield AGIEvent(
                    event_type=AGIEventType.NEW_THREAD_CREATED,
                    event_data=AGIEventNewThreadCreated(
                        agent_id=result.action_data.agent_id,
                        thread_id=run.thread_id,
                    ),
                )
                yield AGIEvent(
                    event_type=AGIEventType.NEW_THREAD_RUN_CREATED,
                    event_data=AGIEventNewThreadRunCreated(
                        agent_id=result.action_data.agent_id,
                        thread_id=run.thread_id,
                        run_id=run.id,
                    ),
                )
                work[
                    tg.create_task(
                        client.beta.threads.runs.retrieve(
                            thread_id=run.thread_id, run_id=run.id
                        )
                    )
                ] = (
                    f"thread.runs.{run.id}",
                    run,
                )
            elif result.action_type == AGIActionType.ADD_MESSAGE:
                continue
                _message = await client.beta.threads.messages.create(
                    thread_id=result.action_data.thread_id,
                    role="user",
                    content=result.action_data.add_message,
                )
        elif work_name.startswith("thread.runs."):
            if result.status == "completed":
                yield AGIEvent(
                    event_type=AGIEventType.THREAD_RUN_COMPLETE,
                    event_data=AGIEventThreadRunComplete(
                        agent_id=result.assistant_id,
                        thread_id=result.thread_id,
                        run_id=result.id,
                        status=result.status,
                    ),
                )
                # TODO Send this similar to seed back to a feedback queue to
                # process as an action for get thread messages
                thread_messages = client.beta.threads.messages.list(
                    thread_id=result.thread_id,
                )
                thread_messages_iter = thread_messages.__aiter__()
                work[tg.create_task(thread_messages_iter.__anext__())] = (
                    f"thread.messages.{result.thread_id}",
                    thread_messages_iter,
                )
            if result.status == "in_progress":
                work[
                    tg.create_task(
                        client.beta.threads.runs.retrieve(
                            thread_id=result.thread_id, run_id=result.id
                        )
                    )
                ] = (
                    f"thread.runs.{run.id}",
                    result,
                )
            else:
                yield AGIEvent(
                    event_type=AGIEventType.THREAD_RUN_EVENT_WITH_UNKNOWN_STATUS,
                    event_data=AGIEventThreadRunEventWithUnknwonStatus(
                        agent_id=result.assistant_id,
                        thread_id=result.thread_id,
                        run_id=result.id,
                        status=result.status,
                    ),
                )
        elif work_name.startswith("thread.messages."):
            _, _, thread_id = work_name.split(".", maxsplit=3)
            work[tg.create_task(work_ctx.__anext__())] = (work_name, work_ctx)
            for content in result.content:
                if content.type == "text":
                    yield AGIEvent(
                        event_type=AGIEventType.NEW_THREAD_MESSAGE,
                        event_data=AGIEventNewThreadMessage(
                            agent_id=result.assistant_id,
                            thread_id=result.thread_id,
                            role=result.role,
                            content_type=content.type,
                            message_content=content.text.value,
                        ),
                    )


def pdb_action_stream_get_user_input():
    try:
        user_input = input("# \r")
    except KeyboardInterrupt:
        sys.exit(0)
    if "(" in user_input:
        user_input = eval(user_input)
    return user_input


class _CURRENTLY_UNDEFINED:
    pass


CURRENTLY_UNDEFINED = _CURRENTLY_UNDEFINED()


class AsyncioLockedCurrentlyDict(collections.UserDict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.currently = CURRENTLY_UNDEFINED
        self.lock = asyncio.Lock()
        self.currently_exists = asyncio.Event()

    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        self.currently = value
        self.currently_exists.set()
        print("currently", value)

    async def __aenter__(self):
        await self.lock.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.lock.__aexit__(exc_type, exc_value, traceback)


async def pdb_action_stream(tg, agents, threads, seed):
    while True:
        for action in seed:
            yield action
        user_input = await asyncio.to_thread(pdb_action_stream_get_user_input)
        if pathlib.Path(user_input).is_file():
            async with agents:
                active_agent_currently_undefined = (
                    agents.currently == CURRENTLY_UNDEFINED
                )
            if active_agent_currently_undefined:
                await tg.create_task(agents.currently_exists.wait())
            async with agents:
                current_agent = agents.currently
            yield AGIAction(
                action_type=AGIActionType.INGEST_FILE,
                action_data=AGIActionIngestFile(
                    agent_id=agents.currently,
                    file_path=user_input,
                ),
            )
        elif not isinstance(user_input, AGIAction):
            async with agents:
                active_agent_currently_undefined = (
                    agents.currently == CURRENTLY_UNDEFINED
                )
            if active_agent_currently_undefined:
                await tg.create_task(agents.currently_exists.wait())
            async with threads:
                active_thread_currently_undefined = (
                    threads.currently == CURRENTLY_UNDEFINED
                )
            if active_thread_currently_undefined:
                async with agents:
                    current_agent = agents.currently
                yield AGIAction(
                    action_type=AGIActionType.NEW_THREAD,
                    action_data=AGIActionNewThread(
                        agent_id=current_agent,
                    ),
                )
                await tg.create_task(threads.currently_exists.wait())
            async with threads:
                current_thread = threads.currently
            yield AGIAction(
                action_type=AGIActionType.ADD_MESSAGE,
                action_data=AGIActionAddMessage(
                    thread_id=current_thread.thread_id,
                    add_message=user_input,
                ),
            )
        else:
            yield user_input


async def main(
    agi_name: str,
    kvstore_service_name: str,
    *,
    kvstore: KVStore = None,
    action_stream: AGIActionStream = None,
    openai_api_key: str = None,
    openai_base_url: Optional[str] = None,
):
    if not kvstore:
        kvstore = KVStoreKeyring({"service_name": kvstore_service_name})

    kvstore_key_agent_id = f"agents.{agi_name}.id"
    action_stream_seed = [
        AGIAction(
            action_type=AGIActionType.NEW_AGENT,
            action_data=AGIActionNewAgent(
                agent_id=await kvstore.get(kvstore_key_agent_id, None),
                agent_name=agi_name,
                agent_instructions=pathlib.Path(__file__)
                .parent.joinpath("openai_assistant_instructions.md")
                .read_text(),
            ),
        ),
    ]

    agents = AsyncioLockedCurrentlyDict()
    threads = AsyncioLockedCurrentlyDict()

    async with kvstore, asyncio.TaskGroup() as tg:
        if not action_stream:
            action_stream = pdb_action_stream(
                tg, agents, threads, action_stream_seed
            )

        if openai_api_key:
            agent_events = agent_openai(
                tg,
                agi_name,
                kvstore,
                action_stream,
                openai_api_key,
                openai_base_url=openai_base_url,
            )
        else:
            raise Exception(
                "No API keys or implementations of assistants given"
            )

        async for agent_event in agent_events:
            pprint.pprint(agent_event)
            print("# ", end="\r")
            if agent_event.event_type in (
                AGIEventType.NEW_AGENT_CREATED,
                AGIEventType.EXISTING_AGENT_RETRIEVED,
            ):
                await kvstore.set(
                    f"agents.{agent_event.event_data.agent_name}.id",
                    agent_event.event_data.agent_id,
                )
                async with agents:
                    agents[
                        agent_event.event_data.agent_name
                    ] = agent_event.event_data.agent_id
                """
                async with threads:
                    active_thread_currently_undefined = (
                        threads.currently == CURRENTLY_UNDEFINED
                    )
                if active_thread_currently_undefined:
                    active_thread_saved = await kvstore.get(
                        f"agents.{agent_event.event_data.agent_id}.current_thread.id",
                        CURRENTLY_UNDEFINED,
                    )
                if active_thread_saved != CURRENTLY_UNDEFINED:
                    threads[active_thread_saved] = AGIEventNewThreadCreated(
                        **json.loads(active_thread_saved)
                    )
                """
            elif agent_event.event_type == AGIEventType.NEW_THREAD_CREATED:
                await kvstore.set(
                    f"agents.{agent_event.event_data.agent_id}.current_thread.id",
                    json.dumps(dataclasses.asdict(agent_event.event_data)),
                )
                async with threads:
                    threads[
                        agent_event.event_data.thread_id
                    ] = agent_event.event_data


if __name__ == "__main__":
    parser = make_argparse_parser()

    args = parser.parse_args(sys.argv[1:])

    import httptest

    with httptest.Server(
        httptest.CachingProxyHandler.to(
            str(openai.AsyncClient(api_key="Alice").base_url),
            state_dir=str(
                pathlib.Path(__file__).parent.joinpath(".cache", "openai")
            ),
        )
    ) as cache_server:
        # args.openai_base_url = cache_server.url()
        asyncio.run(main(**vars(args)))
