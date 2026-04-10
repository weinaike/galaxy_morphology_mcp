import os
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

    def get_galfit_system_message(self):
        filename = "galfit_system_message.md"    
        return self._read_prompt(filename=filename)

    def get_galfits_system_message(self):
        filename = "galfits_system_message.md"    
        return self._read_prompt(filename=filename)

    def get_galfit_analysis_prompt(self, summary_content):
        filename = "galfit_analysis_prompt.md"   
        return self._read_prompt_and_render(filename, summary_content=summary_content)

    def get_galfits_analysis_prompt(self, summary_content, config_content, user_prompt):
        filename = "galfits_analysis_prompt.md"
        return self._read_prompt_and_render(filename,
            summary_content=summary_content,
            config_content=config_content,
            user_instruction=user_prompt or "<None>"
        )

    def get_classification_system_message(self):
        filename = "classification_system_message.md"
        return self._read_prompt(filename=filename)

    def get_classification_prompt(self):
        filename = "classification_prompt.md"
        return self._read_prompt(filename=filename)

    def get_residual_analysis_system_message(self):
        filename = "residual_analysis_message.md"
        return self._read_prompt(filename=filename)

    def get_residual_analysis_prompt(self, summary_content):
        filename = "residual_analysis_prompt.md"
        return self._read_prompt_and_render(filename, summary_content=summary_content)

    def get_component_specification_galfit(self):
        filename = "component_specification_galfit.md"
        return self._read_prompt(filename=filename)

    def get_component_specification_galfits(self):
        filename = "component_specification_galfits.md"
        return self._read_prompt(filename=filename)

    @property
    def GALFIT_SYSTEM_MESSAGE(self):
        return self.get_galfit_system_message()

    @property
    def GALFITS_SYSTEM_MESSAGE(self):
        return self.get_galfits_system_message()

prompts = Prompts()        

if __name__ == '__main__':
    # print(prompts.GALFIT_SYSTEM_MESSAGE)
    # print(prompts.GALFITS_SYSTEM_MESSAGE)
    # print(prompts.get_galfit_analysis_prompt(summary_content="This is summary"))
    # print(prompts.get_galfits_analysis_prompt(summary_content="This is the summary", config_content="Pa1) 1", user_instruction=""))
    print(prompts.get_galfits_analysis_prompt(summary_content="This is the summary", config_content="Pa1) 1", user_prompt="fit the morphology of the given galaxy image"))