"""Cross-sectional context that is independent from a coin's own indicators."""

import statistics

from .config import (
    BREADTH_EXTREME_HIGH,
    BREADTH_EXTREME_LOW,
    CORRELATION_CLUSTER_THRESHOLD,
    MIN_24H_QUOTE_VOLUME,
)


def market_breadth(tickers,min_quote_volume=MIN_24H_QUOTE_VOLUME):
    liquid=[row for symbol,row in tickers.items()
            if symbol.endswith("USDT") and float(row.get("quote_volume",0))>=min_quote_volume]
    if not liquid:
        return {"liquid_count":0,"up_ratio":0.5,"median_change":0.0,
                "volume_weighted_change":0.0,"dispersion":0.0}
    changes=[float(row.get("change",0)) for row in liquid]
    weights=[max(0.0,float(row.get("quote_volume",0))) for row in liquid]
    clipped=[max(-30.0,min(30.0,value)) for value in changes]
    weight_sum=sum(weights)
    return {
        "liquid_count":len(liquid),
        "up_ratio":sum(value>0 for value in changes)/len(changes),
        "median_change":statistics.median(changes),
        "volume_weighted_change":sum(value*weight for value,weight in zip(clipped,weights))/weight_sum if weight_sum else 0.0,
        "dispersion":statistics.pstdev(changes) if len(changes)>1 else 0.0,
    }


def breadth_is_extreme_against(bias,breadth):
    ratio=float(breadth.get("up_ratio",0.5)); median=float(breadth.get("median_change",0))
    if bias=="LONG":
        return ratio<BREADTH_EXTREME_LOW and median<-1.0
    if bias=="SHORT":
        return ratio>BREADTH_EXTREME_HIGH and median>1.0
    return False


def annotate_correlation_clusters(signals,frames_by_symbol,threshold=CORRELATION_CLUSTER_THRESHOLD):
    """Greedy same-direction clustering; it labels risk and never hides signals."""
    representatives=[]; members={}
    for signal in signals:
        frame=frames_by_symbol.get(signal.symbol)
        returns=(frame.close.astype(float).tail(97).pct_change().dropna().reset_index(drop=True)
                 if frame is not None and len(frame)>=25 else None)
        selected=None; selected_corr=0.0
        if returns is not None:
            for cluster_id,side,reference in representatives:
                if side!=signal.side:
                    continue
                count=min(len(reference),len(returns))
                if count<24:
                    continue
                corr=float(reference.tail(count).reset_index(drop=True).corr(returns.tail(count).reset_index(drop=True)))
                if corr>=threshold and corr>selected_corr:
                    selected=cluster_id; selected_corr=corr
        if selected is None:
            selected=len(representatives)+1
            if returns is not None:
                representatives.append((selected,signal.side,returns))
        members.setdefault(selected,[]).append(signal)
        signal.cluster_id=selected
        signal.cluster_correlation=selected_corr
    for cluster_id,cluster in members.items():
        for rank,signal in enumerate(cluster,1):
            signal.cluster_size=len(cluster); signal.cluster_rank=rank
            signal.feature_snapshot.setdefault("portfolio",{}).update({
                "cluster_id":cluster_id,"cluster_size":len(cluster),"cluster_rank":rank,
                "representative_correlation":float(signal.cluster_correlation),
            })
    return signals
