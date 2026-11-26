from .tasks import BaseTask


class ValueTask(BaseTask):
    task_type = "example.value"

    def on_processing(self):
        if self.input.get("skip"):
            return self.finish(reason="No value requested")
        return self.finish({"value": self.input["value"]})


class BatchTask(BaseTask):
    task_type = "example.batch"

    def on_init(self):
        self.child_keys = ("first", "left", "right")
        self.child_total = len(self.child_keys)

    def on_processing(self):
        if self.cur_step == 0:
            return self.wait_for_children(
                [self.child("first", ValueTask, {"value": self.input["seed"]})], ["first"], next_step=1,
            )
        if self.cur_step == 1:
            seed = self.child_output("first")["value"]
            return self.wait_for_children(
                [
                    self.child("left", ValueTask, {"value": seed + 1}),
                    self.child("right", ValueTask, {"skip": self.input.get("skip_right"), "value": seed + 2}),
                ],
                ["left", "right"],
                next_step=2,
            )
        return self.finish({key: self.child_output(key) for key in self.child_keys})
