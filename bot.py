"""Голосовой бот регистратуры учебной поликлиники.

uv run bot.py, затем страница http://localhost:7860/client
"""

import os

from dotenv import load_dotenv
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import (
    EndWorkerFrame,
    TTSSpeakFrame,
    VADParamsUpdateFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute.first_speech_user_mute_strategy import (
    FirstSpeechUserMuteStrategy,
)
from pipecat.workers.runner import WorkerRunner

import clinic
import confirm
from journal import Journal

load_dotenv(override=True)

AUDIO = {"audio_in_enabled": True, "audio_out_enabled": True}
TRANSPORTS = {
    "webrtc": lambda: TransportParams(**AUDIO),
    "eval": lambda: EvalTransportParams(**AUDIO),
}

# Пока пациент не найден, абонент диктует ИИН группами с паузами:
# конец реплики после секунды тишины. Потом 0,2 с, как обычно.
DICTATION = VADParams(stop_secs=1.0)
CONVERSATION = VADParams(stop_secs=0.2)

# Абонент должен знать, что говорит с программой и что разговор записывается.
GREETING = ("Здравствуйте! Регистратура поликлиники номер семь. Вам отвечает "
            "автоматический помощник, разговор записывается. Чем могу помочь?")

# Молчание абонента: через 8 с бот переспрашивает, на третий раз прощается.
SILENCE_SECS = 8
SILENCE = [
    "Вы ещё на линии?",
    "Если удобнее, напишите ответ в окне звонка.",
    "Я вас не слышу. Перезвоните, пожалуйста. До свидания.",
]


def heard(context) -> list[str]:
    """Что сказал абонент: реплики user из контекста, без пересказа модели."""
    return [m["content"] for m in context.messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)]


def run_tool(name, fn):
    async def handler(params):
        args = dict(params.arguments)
        if name in clinic.NEEDS_HEARD:
            args["heard"] = heard(params.context)
        if name == "book":
            args["decision"] = await confirm.decide(args["heard"],
                                                    params.context.messages)
        result = fn(**args)
        if name == "find_patient" and result.get("found"):
            update = VADParamsUpdateFrame(params=CONVERSATION)
            await params.llm.push_frame(update, FrameDirection.UPSTREAM)
        await params.result_callback(result)

    return handler


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, TRANSPORTS)
    clinic.new_call()

    stt = GroqSTTService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqSTTService.Settings(
            model="whisper-large-v3-turbo", language=Language.RU
        ),
    )
    llm = GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqLLMService.Settings(
            model="openai/gpt-oss-20b",
            system_instruction=clinic.SYSTEM,
            reasoning_effort="low",
        ),
    )
    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice="ru_RU-denis-medium")
    )

    for name, fn in clinic.HANDLERS.items():
        llm.register_function(name, run_tool(name, fn))

    @llm.event_handler("on_function_calls_started")
    async def filler(service, calls):
        if any(c.function_name == "find_slots" for c in calls):
            await tts.queue_frame(TTSSpeakFrame("Минутку, смотрю расписание."))

    context = LLMContext(tools=ToolsSchema(standard_tools=clinic.TOOLS))
    user, assistant = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=DICTATION),
            user_idle_timeout=SILENCE_SECS,
            user_mute_strategies=[FirstSpeechUserMuteStrategy()],
        ),
    )

    silent = {"times": 0}

    @user.event_handler("on_user_turn_started")
    async def spoke(aggregator, strategy):
        silent["times"] = 0

    @user.event_handler("on_user_turn_idle")
    async def silence(aggregator):
        silent["times"] += 1
        await aggregator.push_frame(TTSSpeakFrame(SILENCE[silent["times"] - 1]))
        if silent["times"] == len(SILENCE):
            await aggregator.push_frame(EndWorkerFrame())

    pipeline = Pipeline([
        transport.input(),
        stt,
        user,
        llm,
        tts,
        transport.output(),
        assistant,
    ])
    journal = Journal(llm="openai/gpt-oss-20b", tts="ru_RU-denis-medium")
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[journal, journal.latency()],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @worker.rtvi.event_handler("on_client_ready")
    async def greet(rtvi):
        await worker.queue_frames([TTSSpeakFrame(GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def bye(transport, client):
        await runner.cancel()

    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
