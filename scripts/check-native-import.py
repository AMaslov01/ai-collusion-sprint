"""No-call check of the approved data-loader binding and native entry points."""

import json
import os
from pathlib import Path

from collusion_handoff.native_data import install_pinned_loader, base_actor_view, task_actor_view


def main():
    if os.getuid() == 0:
        raise RuntimeError("Requires non-root Docker")
    os.umask(0o077)
    config = json.loads(Path("/code/configs/dev-package.draft.json").read_text())
    install_pinned_loader(Path("/tasks/data.jsonl"), config["data"]["file_hashes"]["sha256"])
    from env.env_systems.travel_env import TravelPlannerEnvironment
    from agent.travel_planner import TravelPlannerAgent
    from run_travel import format_person_plan, parse_all_plans

    os.environ["OPENAI_API_KEY"] = "local-inference"
    os.environ["OPENAI_API_BASE"] = "http://127.0.0.1:18083/v1"
    environment = TravelPlannerEnvironment({"judgement_mode": "none"})
    observation = environment.reset(seed=1)
    assert observation["group_id"] == 1
    base = base_actor_view(observation)
    task = task_actor_view(observation, 1)
    agent = TravelPlannerAgent(model_name="qwen-native-dev", temperature=0.7, max_steps=30, db_path="/database")
    try:
        agent.set_base_person(base["name"], base["query"], format_person_plan(base["name"], base["daily_plans"]))
        assert agent.previous_judgement == ""
        assert agent.base_name == base["name"]
        assert parse_all_plans(agent.accumulated_plans, [task])[0]["name"] == task["name"]
        print(json.dumps({"status": "passed", "model_calls": 0, "groups": len(environment.data), "group_id": observation["group_id"], "actor_task_keys": sorted(task), "actor_base_keys": sorted(base), "native_agent": type(agent).__name__}), flush=True)
    finally:
        agent.client.client.close()
        environment.close()


if __name__ == "__main__":
    main()
