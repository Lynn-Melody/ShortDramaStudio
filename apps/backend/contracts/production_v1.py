"""Production workflow wire contract v1. All times are integer milliseconds."""

from rest_framework import serializers as s
from rest_framework.exceptions import ValidationError


VERSION = "1"
KINDS = ("character", "scene", "prop")
DELIVERY_KINDS = {"video": "video", "subtitles": "subtitles", "timeline": "timeline"}


class Object(s.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and (extra := data.keys() - self.fields.keys()):
            raise ValidationError({key: "Unknown field." for key in sorted(extra)})
        return super().to_internal_value(data)


class Text(s.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class Integer(s.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class Identifier(s.RegexField, Text):
    def __init__(self, **kwargs):
        super().__init__(r"\A[a-zA-Z0-9][a-zA-Z0-9_.:-]*\Z", max_length=100, **kwargs)


class UUID(s.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid", value=data)
        return str(super().to_internal_value(data))


def positive():
    return Integer(min_value=1, max_value=2_147_483_647)


def millis(*, minimum=0):
    return Integer(min_value=minimum, max_value=86_400_000)


def ordered_unique(items, key):
    if len({item[key] for item in items}) != len(items):
        raise ValidationError({key: "Values must be unique."})
    return items


class AssetSpec(Object):
    assetKey = Identifier()
    kind = s.ChoiceField(choices=KINDS)
    title = Text(max_length=200)
    description = Text()


class AssetRef(Object):
    assetKey = Identifier()
    kind = s.ChoiceField(choices=(*KINDS, "image", "audio", *DELIVERY_KINDS.values()))
    versionId = UUID()


class Output(Object):
    slot = Identifier()
    assetKey = Identifier()
    kind = s.ChoiceField(choices=(*KINDS, "image", "audio", *DELIVERY_KINDS.values()))
    title = Text(max_length=200)
    uploadId = UUID()


class EpisodeOutline(Object):
    title = Text(max_length=200)
    outline = Text()


class Line(Object):
    lineId = Identifier()
    speaker = Text(max_length=200)
    text = Text()
    delivery = s.ChoiceField(choices=("lip_sync", "voiceover", "in_world"))


class Sound(Object):
    cueId = Identifier()
    description = Text()


class ShotSpec(Object):
    sceneId = Identifier()
    description = Text()
    estimatedDurationMs = millis(minimum=1)
    assetKeys = s.ListField(child=Identifier())
    dialogue = Line(many=True)
    sound = Sound(many=True)

    def validate_assetKeys(self, keys):
        if len(set(keys)) != len(keys):
            raise ValidationError("Asset keys must be unique.")
        return keys

    def validate_dialogue(self, lines):
        return ordered_unique(lines, "lineId")

    def validate_sound(self, cues):
        return ordered_unique(cues, "cueId")


class EpisodeInput(Object):
    episodeNumber = positive()


class ShotInput(EpisodeInput):
    shotNumber = positive()


class SeriesPlanInput(Object):
    idea = Text()


class SeriesPlanData(Object):
    title = Text(max_length=200)
    world = Text()
    visualGuide = Text()
    episodes = EpisodeOutline(many=True, allow_empty=False)
    assets = AssetSpec(many=True)

    def validate_assets(self, assets):
        return ordered_unique(assets, "assetKey")


class EpisodeScriptInput(EpisodeInput):
    seriesTitle = Text(max_length=200)
    world = Text()
    visualGuide = Text()
    episode = EpisodeOutline()
    assets = AssetSpec(many=True)


class EpisodeScriptData(Object):
    script = Text(trim_whitespace=False)


class EpisodeBreakdownInput(EpisodeInput):
    script = Text(trim_whitespace=False)
    visualGuide = Text()
    assets = AssetSpec(many=True)


class EpisodeBreakdownData(Object):
    shots = ShotSpec(many=True, allow_empty=False)
    assets = AssetSpec(many=True)

    def validate_assets(self, assets):
        return ordered_unique(assets, "assetKey")


class VisualAssetInput(Object):
    asset = AssetSpec()
    visualGuide = Text()


class SelectedSlot(Object):
    selectedSlot = Identifier()


class ShotImageInput(ShotInput):
    shot = ShotSpec()
    visualGuide = Text()
    assets = AssetRef(many=True)


class DialogueInput(ShotInput):
    dialogue = Line(many=True, allow_empty=False)


class DialogueTiming(Object):
    lineId = Identifier()
    startMs = millis()
    endMs = millis(minimum=1)
    durationMs = millis(minimum=1)

    def validate(self, attrs):
        if attrs["endMs"] - attrs["startMs"] != attrs["durationMs"]:
            raise ValidationError("endMs minus startMs must equal durationMs.")
        return attrs


class DialogueTake(DialogueTiming):
    slot = Identifier()


class DialogueData(Object):
    takes = DialogueTake(many=True, allow_empty=False)


class AdoptedDialogue(DialogueTiming):
    delivery = s.ChoiceField(choices=("lip_sync", "voiceover", "in_world"))
    text = Text()
    audio = AssetRef()


class VideoInput(ShotInput):
    sceneId = Identifier()
    description = Text()
    firstFrame = AssetRef()
    lastFrame = AssetRef(allow_null=True)
    lipSync = AdoptedDialogue(many=True)
    targetDurationMs = millis(minimum=1)


class VideoData(SelectedSlot):
    durationMs = millis(minimum=1)


class SoundInput(ShotInput):
    sound = Sound(many=True)
    targetDurationMs = millis(minimum=1)


class SoundTake(Object):
    cueId = Identifier()
    slot = Identifier()


class SoundData(Object):
    takes = SoundTake(many=True)


class AdoptedSound(Object):
    cueId = Identifier()
    audio = AssetRef()


class EditShot(Object):
    shotNumber = positive()
    video = AssetRef()
    durationMs = millis(minimum=1)
    dialogue = AdoptedDialogue(many=True)
    sound = AdoptedSound(many=True)


class EditInput(EpisodeInput):
    title = Text(max_length=200)
    shots = EditShot(many=True, allow_empty=False)


# The result envelope binds to taskType and schemaVersion in the HTTP layer.
# Here the scheduler handles success or failure and supplies the task identity.
LEAVES = {
    "series_plan": ("series_plan", SeriesPlanInput, SeriesPlanData),
    "episode_script": ("episode_script", EpisodeScriptInput, EpisodeScriptData),
    "episode_breakdown": ("episode_breakdown", EpisodeBreakdownInput, EpisodeBreakdownData),
    "character_asset": ("visual_asset", VisualAssetInput, SelectedSlot),
    "scene_asset": ("visual_asset", VisualAssetInput, SelectedSlot),
    "prop_asset": ("visual_asset", VisualAssetInput, SelectedSlot),
    "shot_image": ("shot_image", ShotImageInput, SelectedSlot),
    "shot_dialogue": ("shot_audio", DialogueInput, DialogueData),
    "shot_video": ("shot_video", VideoInput, VideoData),
    "shot_sound": ("shot_audio", SoundInput, SoundData),
    "episode_edit": ("episode_edit", EditInput, Object),
}


def validated(serializer_class, data, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class Success(Object):
    data = s.DictField()
    outputs = Output(many=True)

    def validate_outputs(self, outputs):
        ordered_unique(outputs, "uploadId")
        return ordered_unique(outputs, "slot")


def validate_leaf_input(task_type, value):
    data = validated(LEAVES[task_type][1], value)
    if task_type.endswith("_asset") and data["asset"]["kind"] != task_type.removesuffix("_asset"):
        raise ValidationError("Asset kind must match task type.")
    return data


def _validate_breakdown(data, inputs):
    known = {asset["assetKey"]: asset for asset in inputs["assets"]}
    for asset in data["assets"]:
        key = asset["assetKey"]
        if key in known:
            raise ValidationError({key: "Existing assets must be referenced by key only."})
        known[key] = asset
    for shot in data["shots"]:
        scene = known.get(shot["sceneId"])
        if scene is None or scene["kind"] != "scene":
            raise ValidationError("sceneId must identify a declared scene asset.")
        if set(shot["assetKeys"]) - known.keys():
            raise ValidationError("Each shot asset key must identify a declared asset.")


def _validate_takes(data, inputs, slots, *, input_key, id_key):
    takes = data["takes"]
    if [take[id_key] for take in takes] != [item[id_key] for item in inputs[input_key]]:
        raise ValidationError("Takes must cover the input IDs in input order.")
    if len(takes) != len(slots) or {take["slot"] for take in takes} != slots.keys():
        raise ValidationError("Every take must use a distinct declared output slot.")
    if any(output["kind"] != "audio" for output in slots.values()):
        raise ValidationError("All takes require audio outputs.")
    if input_key == "dialogue":
        if any(left["endMs"] > right["startMs"] for left, right in zip(takes, takes[1:])):
            raise ValidationError("Dialogue takes must be chronological and non-overlapping.")


def validate_leaf_success(task_type, inputs, value):
    output = validated(Success, value)
    data = validated(LEAVES[task_type][2], output["data"])
    slots = {item["slot"]: item for item in output["outputs"]}

    if task_type in ("series_plan", "episode_script", "episode_breakdown"):
        if slots:
            raise ValidationError("This leaf only produces structured data.")
        if task_type == "episode_breakdown":
            _validate_breakdown(data, inputs)
    elif task_type == "episode_edit":
        if {slot: item["kind"] for slot, item in slots.items()} != DELIVERY_KINDS:
            raise ValidationError("Edit must deliver video, subtitles and timeline.")
    elif task_type == "shot_dialogue":
        _validate_takes(data, inputs, slots, input_key="dialogue", id_key="lineId")
    elif task_type == "shot_sound":
        _validate_takes(data, inputs, slots, input_key="sound", id_key="cueId")
    else:
        asset = inputs.get("asset")
        kind = asset["kind"] if asset else {"shot_image": "image", "shot_video": "video"}[task_type]
        if data["selectedSlot"] not in slots or any(item["kind"] != kind for item in slots.values()):
            raise ValidationError("Candidates must match the output kind and include the selected slot.")
        if asset and any(item["assetKey"] != asset["assetKey"] for item in slots.values()):
            raise ValidationError("Candidate asset keys must match the requested asset.")
        if task_type == "shot_video" and data["durationMs"] < inputs["targetDurationMs"]:
            raise ValidationError("The selected video must cover the requested duration.")
    return {**output, "data": data}
