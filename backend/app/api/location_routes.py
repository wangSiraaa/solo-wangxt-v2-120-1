"""定位：运行、列出候选解、读取详情、绑定版本与输入快照。"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import APP_VERSION, DATA_VERSION, PICK_SCHEMA_VERSION
from ..core.location import Arrival, locate
from ..core.velocity import get_model
from ..database import get_db
from ..models.tables import LocationRun, Pick, Scenario
from ..schemas.dto import LocateRequest, RunDetail, RunSummary
from .waveforms import picks_version

router = APIRouter(prefix="/api/scenarios", tags=["location"])


def _effective_pick(p: Pick):
    if p.manual_time_epoch is not None:
        return p.manual_time_epoch, "manual"
    if p.raw_time_epoch is not None:
        return p.raw_time_epoch, "raw"
    return None, "missing"


@router.post("/locate", response_model=RunDetail)
def run_locate(req: LocateRequest, db: Session = Depends(get_db)):
    sc = db.scalar(select(Scenario).where(Scenario.key == req.scenario_key))
    if sc is None:
        raise HTTPException(404, f"案例 {req.scenario_key!r} 不存在")
    phase = req.phase.upper()
    try:
        model = get_model(req.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # —— 排除列表核对：每个 ID 都必须存在、且属于本案例的当前震相，否则整单拒绝。
    #    排除只影响本次运行，不写回 picks 表（raw/manual 均不改动）。——
    excluded = sorted(set(req.exclude_pick_ids))
    if excluded:
        ex_rows = {p.id: p for p in db.scalars(select(Pick).where(Pick.id.in_(excluded)))}
        unknown = [i for i in excluded if i not in ex_rows]
        if unknown:
            raise HTTPException(400, f"要排除的拾取 ID 不存在：{unknown}")
        foreign = [f"#{p.id}({p.station.code}/{p.phase})"
                   for p in ex_rows.values()
                   if p.scenario_id != sc.id or p.phase != phase]
        if foreign:
            raise HTTPException(
                400,
                f"排除的拾取不属于案例 {req.scenario_key!r} 的 {phase} 震相，已拒绝：{foreign}",
            )

    # —— 严格单一震相：只查询该震相的拾取，P/S 物理上不可能混入 ——
    rows = list(db.scalars(select(Pick).where(
        Pick.scenario_id == sc.id, Pick.phase == phase)))

    arrivals: List[Arrival] = []
    snapshot = []
    for p in rows:
        eff, source = _effective_pick(p)
        rec = {
            "pick_id": p.id, "station_code": p.station.code, "phase": p.phase,
            "effective_time_epoch": eff, "time_source": source,
            "raw_status": p.raw_status, "excluded": p.id in excluded,
        }
        snapshot.append(rec)
        if eff is None or p.id in excluded:
            continue
        arrivals.append(Arrival(
            station_code=p.station.code, lon=p.station.lon, lat=p.station.lat,
            time_epoch=eff, pick_id=p.id, time_source=source,
        ))

    result = locate(
        arrivals, phase=phase, model=model, robust=req.robust,
        truth={
            "lon": sc.true_lon, "lat": sc.true_lat, "depth_km": sc.true_depth_km,
            "origin_epoch": sc.true_origin_epoch,
        },
    )
    pv = picks_version(req.scenario_key, db)

    auto_label = f"{phase} {'稳健' if req.robust else 'OLS'} {model.model_id.split('-v')[0]}"
    if excluded:
        auto_label += f" 排除{len(excluded)}站"
    run = LocationRun(
        scenario_id=sc.id,
        label=req.label or auto_label,
        phase=phase, model_id=model.model_id, robust=req.robust,
        locatable=result.locatable, status=result.status, reason=result.reason,
        lon=result.lon, lat=result.lat, depth_km=result.depth_km,
        origin_time_epoch=result.origin_time_epoch,
        rms_s=result.rms_s, max_abs_residual_s=result.max_abs_residual_s,
        residuals=result.residuals, uncertainty=result.uncertainty,
        geometry=result.geometry, truth=result.truth, warnings=result.warnings,
        data_version=DATA_VERSION, pick_schema_version=PICK_SCHEMA_VERSION,
        app_version=APP_VERSION, pick_data_version=pv.pick_data_version,
        input_snapshot=snapshot, excludes=excluded,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _detail(run)


@router.get("/{key}/runs", response_model=List[RunSummary])
def list_runs(key: str, db: Session = Depends(get_db)):
    sc = db.scalar(select(Scenario).where(Scenario.key == key))
    if sc is None:
        raise HTTPException(404, f"案例 {key!r} 不存在")
    rows = db.scalars(
        select(LocationRun).where(LocationRun.scenario_id == sc.id)
        .order_by(LocationRun.id.desc())).all()
    return [_summary(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(LocationRun, run_id)
    if run is None:
        raise HTTPException(404, "定位记录不存在")
    return _detail(run)


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(LocationRun, run_id)
    if run is None:
        raise HTTPException(404, "定位记录不存在")
    db.delete(run)
    db.commit()
    return {"deleted": run_id}


def _summary(run: LocationRun) -> RunSummary:
    n_used = sum(1 for s in run.input_snapshot
                 if s.get("effective_time_epoch") is not None and not s.get("excluded"))
    return RunSummary(
        id=run.id, label=run.label, phase=run.phase, model_id=run.model_id,
        robust=run.robust, locatable=run.locatable, status=run.status, reason=run.reason,
        n_used=n_used, dof=max(n_used - 4, 0), n_excluded=len(run.excludes or []),
        lon=run.lon, lat=run.lat, depth_km=run.depth_km,
        origin_time_epoch=run.origin_time_epoch, rms_s=run.rms_s,
        max_abs_residual_s=run.max_abs_residual_s,
        data_version=run.data_version, pick_data_version=run.pick_data_version,
        created_at=run.created_at,
    )


def _detail(run: LocationRun) -> RunDetail:
    kwargs = _summary(run).model_dump()
    kwargs.update(
        residuals=run.residuals, uncertainty=run.uncertainty,
        geometry=run.geometry, truth=run.truth, warnings=run.warnings,
        pick_schema_version=run.pick_schema_version, app_version=run.app_version,
        input_snapshot=run.input_snapshot, excludes=run.excludes,
    )
    return RunDetail(**kwargs)
