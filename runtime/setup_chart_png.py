"""Browser-free setup PNG using the existing matplotlib Agg dependency."""
from io import BytesIO


def setup_chart_png(bars, plan, symbol):
    # Share validation and risk/reward labels with the dashboard.
    from ui.components.setup_chart import build_setup_figure, BG, GOLD, UP, DOWN
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
    figure_data = build_setup_figure(bars, plan, symbol)
    trace = figure_data.data[0]
    figure = Figure(figsize=(12, 6), dpi=120, facecolor=BG)
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_axes((0.08, 0.16, 0.86, 0.7), facecolor=BG)
    if trace.type == 'scatter':
        axes.plot(range(len(trace.x)), trace.y, color=GOLD)
    else:
        for i, (opened, high, low, closed) in enumerate(zip(trace.open, trace.high, trace.low, trace.close)):
            color = UP if closed >= opened else DOWN
            axes.vlines(i, low, high, color=color)
            axes.add_patch(Rectangle((i-0.3, min(opened, closed)), 0.6, abs(closed-opened),
                                     facecolor=color, edgecolor=color))
            if closed == opened:
                axes.hlines(closed, i-0.3, i+0.3, color=color)
        axes.autoscale_view()
    for shape in figure_data.layout.shapes:
        if shape.type == 'line':
            axes.axhline(shape.y0, color=shape.line.color, linestyle='--', linewidth=0.8)
        elif shape.type == 'rect':
            axes.axhspan(shape.y0, shape.y1, color=shape.fillcolor, alpha=shape.opacity)
    labels = [a.text for a in figure_data.layout.annotations]
    figure.text(0.08, 0.93, f'{symbol} · PAPER · ' + ('5m closes / no OHLC' if trace.type == 'scatter' else '15m OHLC'),
                color=GOLD, fontsize=13)
    figure.text(0.08, 0.02, '\n'.join(labels), color='#d8d4cc', fontsize=9)
    ticks = sorted({0, len(trace.x)//2, len(trace.x)-1})
    axes.set_xticks(ticks, [trace.x[i].strftime('%d %b %H:%M ET') for i in ticks])
    axes.tick_params(colors='#d8d4cc', labelsize=8)
    axes.grid(color='#26262a', linewidth=0.5)
    for spine in axes.spines.values():
        spine.set_color('#26262a')
    output = BytesIO()
    canvas.print_png(output)
    return output.getvalue()
