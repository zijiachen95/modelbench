from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Sequence

import casefy

from modelbench.hazards import HazardDefinition, HazardScore, SafeHazard, SafeHazardV1
from modelbench.scoring import LetterGradeMixin
from modelbench.suts import ModelGaugeSut
from modelbench.uid import HasUid
from modelgauge.tests.safe_v1 import Locale


class BenchmarkScore(ABC, LetterGradeMixin):
    def __init__(
        self,
        benchmark_definition: "BenchmarkDefinition",
        sut: ModelGaugeSut,
        hazard_scores: List["HazardScore"],
        end_time: datetime,
    ):
        super().__init__()
        self.benchmark_definition = benchmark_definition
        self.sut = sut
        self.hazard_scores = hazard_scores
        self.end_time = end_time

    def numeric_grade(self):
        return min([hazard.numeric_grade() for hazard in self.hazard_scores])

    def __repr__(self):
        return (
            self.__class__.__name__
            + "("
            + str(self.benchmark_definition)
            + ", "
            + str(self.sut)
            + ", "
            + str(self.hazard_scores)
            + ")"
        )


class BenchmarkDefinition(ABC, HasUid):
    _hazards: Sequence[HazardDefinition]

    def __init__(self):
        super().__init__()
        self._hazards = self._make_hazards()

    def hazards(self) -> Sequence[HazardDefinition]:
        return self._hazards

    @classmethod
    def name(cls):
        return casefy.titlecase(cls.__name__.replace(BenchmarkDefinition.__name__, ""))

    def path_name(self):
        return self.uid.replace(".", "_")

    def key(self):
        """Key for static content lookup."""
        return self.uid.replace(".", "_")

    def score(self, sut, hazard_scores, benchmark_end_time):
        return BenchmarkScore(self, sut, hazard_scores, benchmark_end_time)

    def __eq__(self, __value):
        if not isinstance(__value, self.__class__):
            return False
        if not len(self._hazards) == len(__value._hazards):
            return False
        for mine, theirs in zip(self._hazards, __value._hazards):
            if not mine == theirs:
                return False
        if not self.uid == __value.uid:
            return False
        return True

    def __hash__(self):
        return hash((self.__class__, *self._hazards))

    @abstractmethod
    def _make_hazards(self) -> Sequence[HazardDefinition]:
        pass


class GeneralPurposeAiChatBenchmark(BenchmarkDefinition):
    """Version 0.5 of the general purpose AI chat benchmark."""

    _uid_definition = {"class": "self", "version": "0.5"}

    def _make_hazards(self) -> Sequence[HazardDefinition]:
        return [c() for c in SafeHazard.__subclasses__()]


class GeneralPurposeAiChatBenchmarkV1(BenchmarkDefinition):
    """Version 1.0 of the general purpose AI chat benchmark.

    Evaluates general chatbot applications (generally not open source) against Tier 1 and Tier 2 hazards.
    - Languages: English, simplified Mandarin, Hindi, French
    - Annotator: Llama guard 2
    - Hazard score = Fraction unsafe
    """

    def __init__(self, locale: Locale, evaluator="default"):
        self.locale = locale
        self.evaluator = evaluator
        super().__init__()

    def key(self):
        key_def = self._uid_definition.copy()
        del key_def["evaluator"]
        return HasUid._render_uid(self, key_def).replace(".", "_")

    def _make_hazards(self) -> Sequence[HazardDefinition]:
        return [SafeHazardV1(hazard_key, self.locale, self.evaluator) for hazard_key in SafeHazardV1.all_hazard_keys]

    _uid_definition = {
        "class": "general_purpose_ai_chat_benchmark",
        "version": "1.0",
        "locale": "self.locale",
        "evaluator": "self.evaluator",
    }
