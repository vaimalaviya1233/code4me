import glob
import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, request, Response, redirect

from limiter import limiter
from model import Model

v1 = Blueprint("v1", __name__)

os.makedirs("data", exist_ok=True)


@v1.route("/prediction/autocomplete", methods=["POST"])
@limiter.limit("1000/hour")
def autocomplete():
    user_token, model, res = get_model()
    if res is not None:
        return res

    values, res = get_body_values(
        request.get_json(),
        [
            ("leftContext", str, False),
            ("rightContext", str, False),
            ("triggerPoint", str, True),
            ("language", str, False),
            ("ide", str, False),
        ],
    )
    if res is not None:
        return res

    left_context = values["leftContext"]
    right_context = values["rightContext"]

    t_before = datetime.now()
    predictions = model.value[1](left_context, right_context)
    t_after = datetime.now()

    verify_token = uuid.uuid4().hex

    with open(f"data/{user_token}-{verify_token}.json", "w+") as f:
        f.write(json.dumps({
            "completionTimestamp": datetime.now().isoformat(),
            "triggerPoint": values["triggerPoint"],
            "language": values["language"].lower(),
            "ide": values["ide"].lower(),
            "model": model.name,
            "predictions": predictions,
            "inferenceTime": (t_after - t_before).total_seconds() * 1000,
            "leftContextLength": len(left_context),
            "rightContextLength": len(right_context)
        }))

    n_suggestions = len(glob.glob(f"data/{user_token}*.json"))
    survey = n_suggestions >= 100 and n_suggestions % 50 == 0

    return response({
        "predictions": predictions,
        "verifyToken": verify_token,
        "survey": survey
    })


@v1.route("/prediction/verify", methods=["POST"])
@limiter.limit("1000/hour")
def verify():
    user_token, model, res = get_model()
    if res is not None:
        return res

    values, res = get_body_values(
        request.get_json(),
        [
            ("verifyToken", str, False),
            ("chosenPrediction", str, True),
            ("groundTruth", str, False),
        ],
    )
    if res is not None:
        return res

    verify_token = values["verifyToken"]
    file_path = f"data/{user_token}-{verify_token}.json"
    if not os.path.exists(file_path):
        return response({
            "error": "Invalid verify token"
        }, status=400)

    with open(file_path, "r+") as completion_file:
        prediction_data = json.load(completion_file)
        if "groundTruth" in prediction_data:
            return response({
                "error": "Already used verify token"
            }, status=400)

        prediction_data["chosenPrediction"] = values["chosenPrediction"]
        prediction_data["groundTruth"] = values["groundTruth"]

        completion_file.seek(0)
        completion_file.write(json.dumps(prediction_data))
        completion_file.truncate()

    return response({
        "success": True
    })


@v1.route("/survey")
def survey():
    user_id = request.args.get("user_id", default="", type=str)
    return redirect(os.getenv("SURVEY_LINK").replace("{user_id}", user_id), code=302)


def get_model():
    authorization = request.headers["Authorization"]
    if not authorization.startswith("Bearer "):
        return None, None, response({
            "error": "Missing bearer token"
        }, status=401)

    user_token = authorization[len("Bearer "):]
    n = int(user_token, 16)
    return user_token, Model(n % len(Model)), None


def get_body_values(body, keys):
    values = {}
    for key, obj, optional in keys:
        value, res = get_body_value(body, key, obj, optional)
        if res is not None:
            return None, res
        values[key] = value
    return values, None


def get_body_value(body, key, obj, optional=False):
    if not optional and key not in body:
        return None, response({
            "error": f"Missing key '{key}' in request body"
        }, status=400)
    value = body.get(key, None)
    if value is not None and not isinstance(value, obj):
        return None, response({
            "error": f"Key '{key}' is not of type '{obj.__name__}'"
        }, status=400)
    return value, None


def response(body, status=200):
    return Response(json.dumps(body, indent=2), mimetype="application/json", status=status)
