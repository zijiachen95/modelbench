import csv
from abc import ABC
from enum import Enum
from typing import Dict, List, Mapping, Optional

from modelgauge.aggregations import get_measurement_stats_by_key
from modelgauge.annotator_set import AnnotatorSet
from modelgauge.base_test import PromptResponseTest
from modelgauge.default_annotator_set import DEFAULT_ANNOTATOR_SET
from modelgauge.dependency_helper import DependencyHelper
from modelgauge.external_data import ExternalData, WebData
from modelgauge.prompt import SUTOptions, TextPrompt
from modelgauge.secret_values import InjectSecret, RequiredSecret, SecretDescription
from modelgauge.single_turn_prompt_response import MeasuredTestItem, PromptWithContext, TestItem, TestItemAnnotations
from modelgauge.sut_capabilities import AcceptsTextPrompt
from modelgauge.test_decorator import modelgauge_test
from modelgauge.test_registry import TESTS
from modelgauge.tests.safe import PersonaResult, SafeTestItemContext, SafeTestResult

PROMPT_SETS = {
    "practice": "airr_official_1.0_practice_prompt_set_release",
    "official": "airr_official_1.0_heldback_prompt_set_release",
}


class ModellabFileDownloadToken(RequiredSecret):
    @classmethod
    def description(cls) -> SecretDescription:
        return SecretDescription(
            scope="modellab_files",
            key="token",
            instructions="Please ask MLCommons admin for permission.",
        )


class Locale(Enum):
    EN_US = "en_US"  # English, United States
    FR_FR = "fr_FR"  # French, France
    ZH_CN = "zh_CN"  # Simplified Chinese, China
    HI_IN = "hi_IN"  # Hindi, India

    @classmethod
    def _missing_(cls, key: object):
        key = str(key).lower()
        for member in cls:
            if member.value.lower() == key:
                return member
        return None


class SafePersonasVersion1(str, Enum):
    NORMAL = "normal"
    SKILLED = "skilled"
    UNSKILLED = "unskilled"


class BaseSafeTestVersion1(PromptResponseTest, ABC):
    """Version 1.0 of SAFE test. This version is still a work in progress and subject to change.

    Concrete tests must set class attribute `annotators`.
    Assumptions about working group deliverables:
    - Prompt files:
        - Filename format:
            safe-[hazard uid]-[timestamp]-[supplier code].csv
        - CSV files include columns:
            Prompt UID, Text, Hazard UID, Locale, Persona
        - There many be multiple personas and locales in one file.
        - There is only one hazard per file.
        - There may be multiple files associated with each hazard.
          These files differ by supplier and have no overlap in prompts.
    - One hazard per test.
    - One locale per test.
    - Tests can have multiple personas (not 100% certain about this).

    Tests will closely follow the hazard taxonomy used by Llama Guard 3 (https://huggingface.co/meta-llama/Llama-Guard-3-8B#hazard-taxonomy-and-policy).
    Test UIDs follow the form "safe-{ hazard acronym }-1.0" (e.g. safe-vcr-1.0). The hazard acronyms are as follows:
    - vcr: Violent Crimes
    - ncr: Non-Violent Crimes
    - src: Sex-Related Crimes
    - cse: Child Sexual Exploitation
    - dfm: Defamation
    - spc: Specialized Advice
    - prv: Privacy Violations
    - ipv: Intellection Property Violations
    - iwp: Indiscriminate Weapons (CBRNE)
    - hte: Hate
    - ssh: Suicide & Self-Harm
    - sxc_prn: Sexual Content; pornographic
    """

    hazards = ["vcr", "ncr", "src", "cse", "dfm", "spc", "prv", "ipv", "iwp", "hte", "ssh", "sxc"]
    annotators: AnnotatorSet

    @staticmethod
    def create_uid(hazard: str, locale: str | Locale, prompt_set: str, evaluator=None):
        assert prompt_set in PROMPT_SETS
        if isinstance(locale, Locale):
            locale = locale.value
        if evaluator is None or evaluator == "default":
            postfix = ""
        else:
            postfix = "-" + evaluator
        return f"safe-{hazard}-{locale}-{prompt_set}-1.0{postfix}".lower()

    def __init__(
        self,
        uid: str,
        hazard: str,
        locale: Locale,
        persona_types: List[SafePersonasVersion1],
        prompt_set: str,
        token: Optional[ModellabFileDownloadToken] = None,
    ):
        self._check_annotators()
        super().__init__(uid)
        assert hazard in self.hazards, f"Invalid hazard type {hazard}."
        self.hazard = hazard
        self.locale = locale
        assert len(persona_types), "At least one persona type must be specified."
        assert len(set(persona_types)) == len(
            persona_types
        ), f"Must specify a unique set of persona types, but got {persona_types}"
        self.persona_types = persona_types
        assert prompt_set in PROMPT_SETS, f"Invalid prompt set {prompt_set}. Must be one of {PROMPT_SETS.keys()}."
        self.prompt_set_file_name = PROMPT_SETS[prompt_set]
        self.token = token

    @classmethod
    def _check_annotators(cls):
        """Check that the annotator set is defined."""
        if not hasattr(cls, "annotators") or cls.annotators is None:
            raise NotImplementedError("Concrete SafeTestVersion1 classes must set class-attribute `annotators`.")

    def get_dependencies(self) -> Mapping[str, ExternalData]:
        modellab_base_download_url = "https://modellab.modelmodel.org/files/download"
        headers = None
        if self.token is not None:
            headers = {"auth-token": self.token.value}
        # Only one dependency.
        return {
            self.prompt_set_file_name: WebData(
                source_url=f"{modellab_base_download_url}/{self.prompt_set_file_name}.csv",
                headers=headers,
            )
        }

    @classmethod
    def get_annotators(cls) -> List[str]:
        cls._check_annotators()
        return cls.annotators.annotators

    def make_test_items(self, dependency_helper: DependencyHelper) -> List[TestItem]:
        sut_options = SUTOptions(
            max_tokens=500,
            temperature=0.01,
        )
        test_items: List[TestItem] = []
        data_file = dependency_helper.get_local_path(self.prompt_set_file_name)
        with open(data_file, "r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                text = row["prompt_text"].strip()
                if not text:
                    continue

                # Check that prompt is for correct hazard/persona/locale.
                hazard = row["hazard"].split("_")[0]
                persona = SafePersonasVersion1(row["persona"])
                locale = Locale(row["locale"])
                if not hazard == self.hazard:
                    continue
                if persona not in self.persona_types:
                    continue
                if locale != self.locale:
                    continue

                prompt = PromptWithContext(
                    prompt=TextPrompt(text=text, options=sut_options),
                    source_id=row["release_prompt_id"],
                )
                test_items.append(
                    TestItem(
                        prompts=[prompt],
                        context=SafeTestItemContext(persona_type=persona),
                    ),
                )
        return test_items

    def measure_quality(self, item: TestItemAnnotations) -> Dict[str, float]:
        return self.annotators.evaluate(item)

    def aggregate_measurements(self, items: List[MeasuredTestItem]) -> SafeTestResult:
        """Returns a SafeTestResult that maps each persona_type's subset of prompts to its corresponding measurements.

        Persona types that do not have any measured test items are not included.
        """
        measurement_stats = get_measurement_stats_by_key(
            "is_safe", items, key=lambda item: item.test_item.context.persona_type
        )
        persona_results = {}
        for persona in self.persona_types:
            if persona in measurement_stats:
                persona_results[persona.value] = PersonaResult(
                    frac_safe=measurement_stats[persona].mean,
                    num_items=measurement_stats[persona].count,
                )
        return SafeTestResult(persona_results=persona_results)


@modelgauge_test(requires_sut_capabilities=[AcceptsTextPrompt])
class SafeTestVersion1(BaseSafeTestVersion1):
    annotators = DEFAULT_ANNOTATOR_SET


ALL_PERSONAS = [
    SafePersonasVersion1.NORMAL,
    SafePersonasVersion1.SKILLED,
    SafePersonasVersion1.UNSKILLED,
]


def register_tests(cls, evaluator=None):
    for locale in [Locale.EN_US]:
        for hazard in cls.hazards:
            for prompt_set in PROMPT_SETS:
                test_uid = BaseSafeTestVersion1.create_uid(hazard, locale, prompt_set, evaluator)
                # TODO: Remove this 'if', duplicates are already caught during registration and should raise errors.
                if not test_uid in TESTS.keys():
                    token = None
                    if prompt_set == "official":
                        token = InjectSecret(ModellabFileDownloadToken)
                    TESTS.register(cls, test_uid, hazard, locale, ALL_PERSONAS, prompt_set, token)


# default llama guard annotator, always
register_tests(SafeTestVersion1)


def register_private_annotator_tests(private_annotators, uid_key):
    try:

        @modelgauge_test(requires_sut_capabilities=[AcceptsTextPrompt])
        class PrivateSafeTestVersion1(BaseSafeTestVersion1):
            annotators = private_annotators

        register_tests(PrivateSafeTestVersion1, uid_key)
    except:
        import traceback

        print(f"unexpected failure registering annotators for {uid_key} and {private_annotators}")
        raise
