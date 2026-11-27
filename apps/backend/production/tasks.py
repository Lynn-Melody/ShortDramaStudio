from contracts.production_v1 import MAX_DURATION_MS, VERSION
from scheduler.tasks import BaseTask

from . import leaves


DIALOGUE_TAIL_MS = 250


class ProductionTask(BaseTask):
    definition_version = VERSION

    def wait_for_stage(self, key, task_class, inputs):
        previous = (self.child_keys[self.cur_step - 1],) if self.cur_step else ()
        return self.wait_for_children(
            [self.child(key, task_class, inputs, depends_on=previous)], [key], self.cur_step + 1,
        )


class ShortDrama(ProductionTask):
    task_type = "short_drama"
    stages = ("plan", "global_assets", "episodes")

    def on_init(self):
        self.child_keys = self.stages
        self.child_total = len(self.child_keys)

    def on_processing(self):
        if self.cur_step == 0:
            return self.wait_for_stage("plan", leaves.SeriesPlan, self.input)
        plan = self.child_output("plan")
        if self.cur_step == 1:
            return self.wait_for_stage("global_assets", GlobalAssets, {
                "assets": plan["assets"], "visualGuide": plan["visualGuide"],
            })
        if self.cur_step == 2:
            return self.wait_for_stage("episodes", AllEpisodes, {
                "plan": plan, "versions": self.child_output("global_assets")["versions"],
            })
        return self.finish({"title": plan["title"], "episodes": self.child_output("episodes")["episodes"]})


class AssetBatch(ProductionTask):
    def on_init(self):
        self.child_keys = tuple(asset["assetKey"] for asset in self.input["assets"])
        self.child_total = len(self.child_keys)

    def on_processing(self):
        if not self.child_keys:
            return self.finish({"versions": {}}, reason="No missing visual assets")
        if self.cur_step == 0:
            context = {key: value for key, value in self.input.items() if key != "assets"}
            children = [
                self.child(asset["assetKey"], leaves.VISUAL_ASSETS[asset["kind"]], {
                    **context, "asset": asset,
                })
                for asset in self.input["assets"]
            ]
            return self.wait_for_children(children, self.child_keys, next_step=1)
        return self.finish({"versions": {key: self.child_output(key)["asset"] for key in self.child_keys}})


class GlobalAssets(AssetBatch):
    task_type = "global_assets"


class EpisodeAssets(AssetBatch):
    task_type = "episode_assets"


class AllEpisodes(ProductionTask):
    task_type = "all_episodes"

    def on_init(self):
        self.child_total = len(self.input["plan"]["episodes"])
        self.child_keys = tuple(str(n) for n in range(1, self.child_total + 1))

    def on_processing(self):
        plan = self.input["plan"]
        index = self.cur_step
        if index == len(plan["episodes"]):
            return self.finish({"episodes": [self.child_output(str(n))["delivery"] for n in range(1, index + 1)]})

        if index == 0:
            assets, versions = plan["assets"], self.input["versions"]
        else:
            previous = self.child_output(str(index))
            assets, versions = previous["assets"], previous["versions"]
        return self.wait_for_stage(str(index + 1), MakeEpisode, {
            "episodeNumber": index + 1,
            "episode": plan["episodes"][index],
            "seriesTitle": plan["title"],
            "visualGuide": plan["visualGuide"],
            "world": plan["world"],
            "characters": plan.get("characters", []),
            "assets": assets,
            "versions": versions,
        })


class MakeEpisode(ProductionTask):
    task_type = "make_episode"
    stages = ("script", "breakdown", "assets", "images", "shots", "edit")

    def on_init(self):
        self.child_keys = self.stages
        self.child_total = len(self.child_keys)

    def on_processing(self):
        scope = {"episodeNumber": self.input["episodeNumber"]}
        guide = self.input["visualGuide"]
        if self.cur_step == 0:
            return self.wait_for_stage("script", leaves.EpisodeScript, {
                **scope, "seriesTitle": self.input["seriesTitle"], "world": self.input["world"],
                "episode": self.input["episode"], "visualGuide": guide, "assets": self.input["assets"],
                "characters": self.input["characters"],
            })
        if self.cur_step == 1:
            return self.wait_for_stage("breakdown", leaves.EpisodeBreakdown, {
                **scope, "script": self.child_output("script")["script"], "visualGuide": guide,
                "assets": self.input["assets"],
            })
        breakdown = self.child_output("breakdown")
        if self.cur_step == 2:
            return self.wait_for_stage("assets", EpisodeAssets, {
                **scope, "assets": breakdown["assets"], "visualGuide": guide,
            })
        versions = {**self.input["versions"], **self.child_output("assets")["versions"]}
        if self.cur_step == 3:
            return self.wait_for_stage("images", AllShotImages, {
                **scope, "shots": breakdown["shots"], "visualGuide": guide, "versions": versions,
            })
        if self.cur_step == 4:
            return self.wait_for_stage("shots", AllShots, {
                **scope, "shots": breakdown["shots"], "images": self.child_output("images")["shots"],
            })
        if self.cur_step == 5:
            return self.wait_for_stage("edit", leaves.EpisodeEdit, {
                **scope, "title": self.input["episode"]["title"], "shots": self.child_output("shots")["shots"],
            })
        return self.finish({
            "assets": [*self.input["assets"], *breakdown["assets"]], "versions": versions,
            "delivery": {**scope, **self.child_output("edit")},
        })


class ShotBatch(ProductionTask):
    def on_init(self):
        self.child_total = len(self.input["shots"])
        self.child_keys = tuple(str(n) for n in range(1, self.child_total + 1))

    def on_processing(self):
        if not self.input["shots"]:
            return self.finish({"shots": []}, reason="No shots in batch")
        if self.cur_step == 0:
            children = [
                self.child(str(n), self.leaf_task, self.shot_input(n, shot))
                for n, shot in enumerate(self.input["shots"], 1)
            ]
            return self.wait_for_children(children, [child.key for child in children], next_step=1)
        return self.finish({"shots": [
            self.child_output(str(n)) for n in range(1, len(self.input["shots"]) + 1)
        ]})

    def shot_scope(self, number):
        return {"episodeNumber": self.input["episodeNumber"], "shotNumber": number}


class AllShotImages(ShotBatch):
    task_type = "all_shot_images"
    leaf_task = leaves.ShotImage

    def shot_input(self, number, shot):
        keys = dict.fromkeys([shot["sceneId"], *shot["assetKeys"]])
        return {
            **self.shot_scope(number), "shot": shot, "visualGuide": self.input["visualGuide"],
            "assets": [self.input["versions"][key] for key in keys],
        }


class MakeShot(ProductionTask):
    task_type = "make_shot"
    stages = ("dialogue", "video", "sound")

    def on_init(self):
        self.child_keys = self.stages
        self.child_total = len(self.child_keys)

    def on_processing(self):
        shot = self.input["shot"]
        scope = {key: self.input[key] for key in ("episodeNumber", "shotNumber")}
        if self.cur_step == 0:
            return self.wait_for_stage("dialogue", leaves.ShotDialogue, {**scope, "dialogue": shot["dialogue"]})

        dialogue = self.child_output("dialogue")["dialogue"]
        if self.cur_step == 1:
            duration = shot["estimatedDurationMs"]
            if dialogue:
                duration = max(duration, min(MAX_DURATION_MS, dialogue[-1]["endMs"] + DIALOGUE_TAIL_MS))
            return self.wait_for_children(
                [
                    self.child("video", leaves.ShotVideo, {
                        **scope, "sceneId": shot["sceneId"], "description": shot["description"],
                        "firstFrame": self.input["firstFrame"], "lastFrame": self.input["lastFrame"],
                        "lipSync": [line for line in dialogue if line["delivery"] == "lip_sync"],
                        "targetDurationMs": duration,
                    }, depends_on=("dialogue",)),
                    self.child("sound", leaves.ShotSound, {
                        **scope, "sound": shot["sound"], "targetDurationMs": duration,
                    }, depends_on=("dialogue",)),
                ],
                ["video", "sound"],
                next_step=2,
            )
        return self.finish({
            "shotNumber": scope["shotNumber"], "dialogue": dialogue,
            **self.child_output("video"), **self.child_output("sound"),
        })


class AllShots(ShotBatch):
    task_type = "all_shots"
    leaf_task = MakeShot

    def shot_input(self, number, shot):
        shots = self.input["shots"]
        images = self.input["images"]
        same_scene = number < len(shots) and shot["sceneId"] == shots[number]["sceneId"]
        return {
            **self.shot_scope(number), "shot": shot,
            "firstFrame": images[number - 1]["image"],
            "lastFrame": images[number]["image"] if same_scene else None,
        }
