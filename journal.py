"""Журнал звонка: одна строка JSON на событие, время от начала звонка.

Файл runs/call_<время>.jsonl читает report.py.
"""

import json
import time
from datetime import datetime
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.frameworks.rtvi.frames import RTVISendTextFrame

RUNS = Path(__file__).parent / "runs"


class Journal(BaseObserver):
    def __init__(self, **config):
        super().__init__(observe_every_push=False)
        RUNS.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.path = RUNS / f"call_{stamp}.jsonl"
        self.t0 = time.time()
        self.file = self.path.open("w", encoding="utf-8")
        self.context = None
        self.write("config", **config)

    def write(self, event: str, **data):
        row = {"t": round(time.time() - self.t0, 3), "ev": event, **data}
        self.file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.file.flush()

    async def on_push_frame(self, data: FramePushed):
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        f = data.frame
        if isinstance(f, VADUserStartedSpeakingFrame):
            self.write("user_start")
        elif isinstance(f, VADUserStoppedSpeakingFrame):
            self.write("user_stop", stop_secs=f.stop_secs)
        elif isinstance(f, TranscriptionFrame):
            self.write("heard", text=f.text)
        elif isinstance(f, RTVISendTextFrame):
            self.write("typed", text=f.text)
        elif isinstance(f, LLMFullResponseStartFrame):
            self.write("llm_start")
        elif isinstance(f, FunctionCallInProgressFrame):
            self.write("tool", name=f.function_name, args=f.arguments)
        elif isinstance(f, FunctionCallResultFrame):
            self.write("tool_result", name=f.function_name, result=f.result)
        elif isinstance(f, TTSTextFrame):
            self.write("said", text=f.text)
        elif isinstance(f, BotStartedSpeakingFrame):
            self.write("bot_start")
        elif isinstance(f, BotStoppedSpeakingFrame):
            self.write("bot_stop")
        elif isinstance(f, InterruptionFrame):
            self.write("interrupted")
        elif isinstance(f, LLMContextFrame):
            self.context = f.context
        elif isinstance(f, (EndFrame, CancelFrame)) and self.context:
            # что осталось в истории модели к концу звонка
            self.write("context", messages=self.context.messages)
            self.context = None
        elif isinstance(f, MetricsFrame):
            for d in f.data:
                if isinstance(d, LLMUsageMetricsData):
                    self.write("tokens", total=d.value.total_tokens)

    def latency(self) -> UserBotLatencyObserver:
        """Наблюдатель Pipecat: раскладывает паузу перед ответом по частям."""
        obs = UserBotLatencyObserver()

        @obs.event_handler("on_latency_breakdown")
        async def on_breakdown(_, breakdown):
            self.write("breakdown", total=round(breakdown.total_secs, 3),
                       lines=breakdown.turn_contribution_lines())

        return obs
