"""Тот же звонок на Gemini Live: речь в речь, те же инструменты из clinic.py.

uv run bot_live.py, затем страница http://localhost:7860/client
"""

import os

from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.workers.runner import WorkerRunner

import clinic
from bot import TRANSPORTS, run_tool
from journal import Journal


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, TRANSPORTS)
    clinic.new_call()
    tools = ToolsSchema(standard_tools=clinic.TOOLS)

    llm = GeminiLiveLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        tools=tools,
        settings=GeminiLiveLLMService.Settings(
            model="models/gemini-3.8-live",
            system_instruction=clinic.SYSTEM,
            language="ru-RU",
        ),
    )
    for name, fn in clinic.HANDLERS.items():
        llm.register_function(name, run_tool(name, fn))

    context = LLMContext(tools=tools)
    user, assistant = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    pipeline = Pipeline(
        [transport.input(), user, llm, transport.output(), assistant]
    )

    journal = Journal(llm="gemini-3.8-live")
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[journal, journal.latency()],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @worker.rtvi.event_handler("on_client_ready")
    async def greet(rtvi):
        context.add_message(
            {"role": "developer", "content": "Поздоровайся одной фразой."}
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def bye(transport, client):
        await runner.cancel()

    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
