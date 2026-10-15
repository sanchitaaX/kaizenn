"""Quests and rewards API: GET /api/game for the level bar, quests and rewards; the "Guess the bug" quiz for an
agent run. Everything answers 409 while the user has Quests & rewards switched off (except GET /api/game)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import game
from app.agent import controller, rules
from app.api import same_origin
from app.auth import require_user

router = APIRouter(prefix="/api/game", dependencies=[Depends(same_origin)])


class QuizIn(BaseModel):
    choice: str = Field(..., max_length=40)


@router.get("")
def game_state(user: dict = Depends(require_user)):
    return game.state(user["login"])


def _seed_or_error(run_id: int, user: dict) -> dict:
    if not game.enabled(user["login"]):
        raise HTTPException(status_code=409, detail="Turn on Quests & rewards on your Profile first.")
    run = controller.get(run_id, user["login"])
    seed = ((run or {}).get("state") or {}).get("result", {}).get("quiz_seed")
    if not seed:
        raise HTTPException(status_code=404, detail="There is no quiz for this run yet.")
    return seed


@router.get("/quiz/{run_id}")
def quiz(run_id: int, user: dict = Depends(require_user)):
    seed = _seed_or_error(run_id, user)
    return {**game.quiz_question(run_id, seed), "answered": game.quiz_result(user["login"], run_id, seed)}


@router.post("/quiz/{run_id}")
def answer(run_id: int, body: QuizIn, user: dict = Depends(require_user)):
    seed = _seed_or_error(run_id, user)
    if body.choice not in rules.TYPES:
        raise HTTPException(status_code=422, detail="Pick one of the choices.")
    return game.answer_quiz(user["login"], run_id, seed, body.choice)
