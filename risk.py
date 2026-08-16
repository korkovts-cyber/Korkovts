def position_size(balance,risk_pct,entry,stop):
    risk_money=balance*risk_pct/100
    distance=abs(entry-stop)
    if distance<=0: raise ValueError("entry and stop must differ")
    return risk_money/distance

def conservative_plan(signal,balance,risk_pct,round_trip_cost_pct=.12):
    entry=signal.entry_high if signal.side=="LONG" else signal.entry_low
    distance=abs(entry-signal.stop); cost=entry*round_trip_cost_pct/100
    volatility=float(getattr(signal,"volatility_pct",0))
    factor=.5 if volatility>=2 else (.75 if volatility>=1.2 else 1.0)
    effective_risk_pct=float(risk_pct)*factor
    risk_budget=float(balance)*effective_risk_pct/100
    raw_qty=risk_budget/(distance+cost) if distance+cost else 0
    qty=min(raw_qty,float(balance)/entry) if entry else 0
    return {"entry":entry,"qty":qty,"notional":qty*entry,"risk_budget":risk_budget,
            "actual_risk":qty*(distance+cost),"effective_risk_pct":effective_risk_pct,
            "cost_pct":round_trip_cost_pct}
