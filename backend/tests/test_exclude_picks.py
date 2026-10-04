"""验收测试：排除拾取（exclude picks）能力。

1. outlier 案例排除可疑 P 台站（S04）后，新旧候选解都可查看且输入快照不同；
2. 排除到不足四个有效台站时明确不可定位；
3. 传入其他案例或震相的拾取 ID 被拒绝；
4. 排除只影响本次运行，raw / manual 拾取不被修改。
"""
import os
import sys
import tempfile

os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

seed()
client = TestClient(app)

MODEL = "homog-crust-6.0-3.46-v1"
passed = []


def check(name, cond, extra=""):
    assert cond, f"FAIL: {name} {extra}"
    passed.append(name)
    print(f"  ok - {name} {extra}")


def locate(key, phase, excludes=None):
    return client.post("/api/scenarios/locate", json={
        "scenario_key": key, "phase": phase, "model_id": MODEL,
        "robust": False, "label": "", "exclude_pick_ids": excludes or [],
    })


picks = client.get("/api/scenarios/outlier/picks").json()
p_picks = {p["station_code"]: p for p in picks if p["phase"] == "P"}
s04 = p_picks["S04"]
assert s04["raw_status"] == "shifted_outlier"
print(f"outlier P 拾取: {len(p_picks)} 条, 可疑台站 S04 pick_id={s04['id']}")

# —— 1. 基线（不排除）与排除 S04 的两次运行 ——
r_base = locate("outlier", "P")
check("基线运行成功", r_base.status_code == 200, r_base.text[:200])
base = r_base.json()
check("基线 6 台全部纳入", base["n_used"] == 6 and base["locatable"])

r_excl = locate("outlier", "P", [s04["id"]])
check("排除运行成功", r_excl.status_code == 200, r_excl.text[:200])
excl = r_excl.json()
check("排除后 5 台参与", excl["n_used"] == 5 and excl["locatable"])
check("排除记录写入 run.excludes", excl["excludes"] == [s04["id"]])
check("摘要带 n_excluded=1", excl["n_excluded"] == 1)

snap_base = {s["pick_id"]: s for s in base["input_snapshot"]}
snap_excl = {s["pick_id"]: s for s in excl["input_snapshot"]}
check("两次运行输入快照不同",
      base["input_snapshot"] != excl["input_snapshot"])
check("新快照中 S04 标记 excluded=true", snap_excl[s04["id"]]["excluded"] is True)
check("旧快照中 S04 标记 excluded=false", snap_base[s04["id"]]["excluded"] is False)
check("其余台站快照一致", all(
    snap_base[i] == snap_excl[i] for i in snap_base if i != s04["id"]))
check("排除后解更靠近真值",
      excl["truth"]["horizontal_error_km"] < base["truth"]["horizontal_error_km"],
      f"({base['truth']['horizontal_error_km']} -> {excl['truth']['horizontal_error_km']} km)")

# 新旧候选解都可在列表与详情接口查看
runs = client.get("/api/scenarios/outlier/runs").json()
ids = {r["id"] for r in runs}
check("候选列表同时含新旧解", {base["id"], excl["id"]} <= ids)
for rid in (base["id"], excl["id"]):
    d = client.get(f"/api/scenarios/runs/{rid}")
    check(f"详情可查 run#{rid}", d.status_code == 200 and d.json()["input_snapshot"])
d_excl = client.get(f"/api/scenarios/runs/{excl['id']}").json()
check("详情快照保留排除标记",
      any(s["excluded"] for s in d_excl["input_snapshot"]))
check("列表摘要含排除计数",
      next(r for r in runs if r["id"] == excl["id"])["n_excluded"] == 1)

# —— 2. 排除到不足 4 个有效台站 → 明确不可定位 ——
three_more = [p_picks[c]["id"] for c in ("S01", "S02", "S03")]
r_few = locate("outlier", "P", [s04["id"]] + three_more)
check("不足台站运行仍返回 200（保存不可定位记录）", r_few.status_code == 200)
few = r_few.json()
check("locatable=false", few["locatable"] is False)
check("无坐标输出", few["lon"] is None and few["lat"] is None)
check("原因说明台站不足", "至少" in (few["reason"] or "") and "4" in few["reason"],
      few["reason"])
check("不可定位运行同样入库可查",
      client.get(f"/api/scenarios/runs/{few['id']}").json()["locatable"] is False)

# —— 3. 其他案例 / 其他震相的拾取 ID 被拒绝 ——
nominal_p = [p for p in client.get("/api/scenarios/nominal/picks").json()
             if p["phase"] == "P"][0]
r_cross = locate("outlier", "P", [nominal_p["id"]])
check("跨案例排除 ID 被 400 拒绝", r_cross.status_code == 400, r_cross.text[:160])

s_phase = [p for p in picks if p["phase"] == "S"][0]
r_phase = locate("outlier", "P", [s_phase["id"]])
check("跨震相排除 ID 被 400 拒绝", r_phase.status_code == 400, r_phase.text[:160])

r_ghost = locate("outlier", "P", [999999])
check("不存在的排除 ID 被 400 拒绝", r_ghost.status_code == 400, r_ghost.text[:160])

n_runs_before = len(client.get("/api/scenarios/outlier/runs").json())
check("被拒绝的请求未产生新运行",
      len(client.get("/api/scenarios/outlier/runs").json()) == n_runs_before)

# —— 4. 排除不修改 raw / manual 拾取 ——
after = {p["id"]: p for p in client.get("/api/scenarios/outlier/picks").json()}
s04_after = after[s04["id"]]
check("raw 拾取未改动", s04_after["raw_time_epoch"] == s04["raw_time_epoch"]
      and s04_after["raw_status"] == "shifted_outlier")
check("manual 拾取未改动", s04_after["manual_time_epoch"] is None)
v = client.get("/api/scenarios/outlier/picks/version").json()
check("拾取数据版本未因排除变化", v["n_manual"] == 0)

print(f"\n全部 {len(passed)} 项验收通过 ✔")
