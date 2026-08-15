def position_size(balance,risk_pct,entry,stop):
    risk_money=balance*risk_pct/100
    distance=abs(entry-stop)
    if distance<=0: raise ValueError("entry and stop must differ")
    return risk_money/distance
