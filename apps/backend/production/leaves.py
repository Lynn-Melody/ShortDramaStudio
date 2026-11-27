from datetime import timedelta

from contracts import production_v1 as v1
from scheduler.tasks import BaseTask, Failed, RetryPolicy

from .completion import record_workflow_completion


class WorkflowLeaf(BaseTask):
    definition_version = v1.VERSION
    timeout = timedelta(minutes=30)
    retry_policy = RetryPolicy(retryable_error_codes=v1.RETRYABLE_ERROR_CODES)
    workflow_completion_handler = staticmethod(record_workflow_completion)

    def on_init(self):
        v1.validate_leaf_input(self.task_type, self.input)

    def on_processing(self):
        inputs = v1.validate_leaf_input(self.task_type, self.input)
        return self.invoke_workflow(v1.LEAVES[self.task_type][0], inputs)

    def on_workflow_result(self, result):
        if isinstance(result, Failed):
            return result
        inputs = v1.validate_leaf_input(self.task_type, self.input)
        return self.finish(v1.validate_leaf_success(self.task_type, inputs, result.output))


class SeriesPlan(WorkflowLeaf):
    task_type = "series_plan"


class EpisodeScript(WorkflowLeaf):
    task_type = "episode_script"


class EpisodeBreakdown(WorkflowLeaf):
    task_type = "episode_breakdown"


class CharacterAsset(WorkflowLeaf):
    task_type = "character_asset"


class SceneAsset(WorkflowLeaf):
    task_type = "scene_asset"


class PropAsset(WorkflowLeaf):
    task_type = "prop_asset"


VISUAL_ASSETS = {"character": CharacterAsset, "scene": SceneAsset, "prop": PropAsset}


class ShotImage(WorkflowLeaf):
    task_type = "shot_image"


class ShotDialogue(WorkflowLeaf):
    task_type = "shot_dialogue"

    def on_init(self):
        v1.validated(v1.DialogueTaskInput, self.input)

    def on_processing(self):
        if not self.input["dialogue"]:
            return self.finish({"dialogue": []}, reason="No dialogue in shot")
        return super().on_processing()


class ShotVideo(WorkflowLeaf):
    task_type = "shot_video"


class ShotSound(WorkflowLeaf):
    task_type = "shot_sound"


class EpisodeEdit(WorkflowLeaf):
    task_type = "episode_edit"
