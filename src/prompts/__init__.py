import os
import re
import abc

class SingletonMeta(abc.ABCMeta):
    _instances = {}
    _lock = __import__('threading').Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
            return cls._instances[cls]

class Prompts(metaclass=SingletonMeta):
    _CACHED_MESSAGES = {}
    _ROOT = os.path.abspath(os.path.dirname(__file__))

    def _get_file_path(self, filename):
        return os.path.join(self._ROOT, filename)

    def _read_prompt(self, filename):
        filename = self._get_file_path(filename)
        if filename in self._CACHED_MESSAGES:
            return self._CACHED_MESSAGES[filename]

        with open(filename) as f:
            content = f.read().strip()
        self._CACHED_MESSAGES[filename] = content
        return content

    def _read_prompt_and_render(self, filename, **kwargs):
        filename = self._get_file_path(filename)
        prompt = self._read_prompt(filename)
        # Use str.replace instead of str.format to avoid interpreting
        # literal braces in the template (e.g. JSON examples) as format
        # placeholders.
        for key, value in kwargs.items():
            prompt = prompt.replace('{' + key + '}', str(value) if value is not None else '')
        return prompt

    def _read_phases(self, filename):
        """Read a multi-phase prompt file split by `<!-- phase:name -->` markers.

        The first section (before any marker) is keyed ``_first``.
        Returns a dict mapping phase names to stripped content strings.
        """
        filepath = self._get_file_path(filename)
        if filepath in self._CACHED_MESSAGES:
            return self._CACHED_MESSAGES[filepath]

        with open(filepath) as f:
            raw = f.read()

        parts = re.split(r'<!--\s*phase:(\w+)\s*-->', raw)
        # parts: [before_first, name1, content1, name2, content2, ...]
        phases: dict[str, str] = {}
        if parts[0].strip():
            phases["_first"] = parts[0].strip()
        for i in range(1, len(parts), 2):
            name = parts[i]
            content = parts[i + 1].strip() if i + 1 < len(parts) else ""
            phases[name] = content

        self._CACHED_MESSAGES[filepath] = phases
        return phases

    def get_classification_system_message(self):
        filename = "classification_system_message.md"
        return self._read_prompt(filename=filename)

    def get_classification_prompt(self):
        filename = "classification_prompt.md"
        return self._read_prompt(filename=filename)

    def get_residual_analysis_system_message(self):
        filename = "residual_analysis_message.md"
        return self._read_prompt(filename=filename)

    def get_component_specification_galfit(self):
        filename = "component_specification_galfit.md"
        return self._read_prompt(filename=filename)

    def get_component_specification_galfits(self):
        filename = "component_specification_galfits.md"
        return self._read_prompt(filename=filename)

    # --- Phase templates for component analysis ---
    # All stored in a single file: component_analysis_phases.md

    def _ca_phases(self):
        return self._read_phases("residual_analysis_prompt.md")

    def get_phase_visual_extraction(self):
        return self._ca_phases()["_first"]

    def get_phase_parameter_review(self, summary_content, custom_instructions=""):
        template = self._ca_phases()["parameter_review"]
        for key, value in [("summary_content", summary_content), ("custom_instructions", custom_instructions)]:
            template = template.replace("{" + key + "}", str(value) if value is not None else "")
        return template

    def get_phase_expert_reasoning(self):
        return self._ca_phases()["expert_reasoning"]

    def get_phase_decision_output(self):
        return self._ca_phases()["decision_output"]

    # --- Beam Search candidate generation phases ---
    # Stored in beam_action_generation_prompt.md with markers:
    # _first = visual extraction, candidate_generation = candidate output spec

    def _bag_phases(self):
        return self._read_phases("beam_action_generation_prompt.md")

    def get_beam_visual_extraction(self):
        return self._bag_phases()["_first"]

    def get_beam_candidate_generation(self, summary_content, custom_instructions="",
                                      branch_id="", parent_label="", depth=1):
        template = self._bag_phases()["candidate_generation"]
        for key, value in [("summary_content", summary_content),
                            ("custom_instructions", custom_instructions),
                            ("branch_id", branch_id),
                            ("parent_label", parent_label),
                            ("depth", depth)]:
            template = template.replace("{" + key + "}", str(value) if value is not None else "")
        return template

    # --- Best-round comparison prompt (visual-residual primary, metrics reference) ---

    def get_round_comparison_prompt(self, best_round_label, current_round_label,
                                    best_reference, current_reference):
        return self._read_prompt_and_render(
            "round_comparison.md",
            best_round_label=best_round_label,
            current_round_label=current_round_label,
            best_reference=best_reference,
            current_reference=current_reference,
        )

prompts = Prompts()
