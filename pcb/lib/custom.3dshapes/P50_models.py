"""Multicomp P50-B-120-G probe and R50-SC solder receptacle, from the Farnell datasheets (mm).
Two assemblies, both with z=0 = top surface of the LOWER (solder) plate, tip pointing +Z:
  A: receptacle (collar hangs under the upper plate) + probe      -> P50_R50SC_Assembly.step
  B: probe soldered straight into the lower plate                 -> P50-B-120-G_Direct.step
Edit the numbers below and re-run with build123d to regenerate.
"""
from build123d import *
BOARD_T = 1.6                       # lower plate thickness
def cyl(d, z0, z1):
    return Pos(0, 0, z0) * Cylinder(d/2, z1-z0, align=(Align.CENTER, Align.CENTER, Align.MIN))
def cone(d0, d1, z0, z1):
    return Pos(0, 0, z0) * Cone(d0/2, d1/2, z1-z0, align=(Align.CENTER, Align.CENTER, Align.MIN))
gold  = Color(0.85, 0.68, 0.22)
steel = Color(0.78, 0.78, 0.80)

def probe(z_rear):
    """P50-B-120-G: barrel D0.68 x 13.0, plunger D0.5 (2.65 travel) + 0.9 head with point. Total 16.55."""
    barrel  = cyl(0.68, z_rear, z_rear+13.0)
    shaft   = cyl(0.50, z_rear+13.0, z_rear+13.0+2.65+0.5)
    tip     = cone(0.50, 0.10, z_rear+13.0+2.65+0.5, z_rear+16.55)
    barrel.color, barrel.label = gold, "barrel"
    shaft.color,  shaft.label  = steel, "plunger"
    tip.color,    tip.label    = steel, "tip"
    return [barrel, shaft, tip]

def receptacle(z_open):
    """R50-SC: open end at z_open; collar D0.97 starts 2.5 below; body D0.86 to 12.7; tail D0.75 to 17.7."""
    body   = cyl(0.86, z_open-12.7, z_open)
    collar = cyl(0.97, z_open-2.9, z_open-2.5)
    tail   = cyl(0.75, z_open-17.7, z_open-12.7)
    for s, n in ((body, "body"), (collar, "collar"), (tail, "tail")):
        s.color, s.label = gold, "receptacle_"+n
    return [body, collar, tail]

# A: receptacle tail end sticks out 2.2 mm below the lower plate; tail/body step 1.2 mm above it
z_tail_end = -BOARD_T - 2.2
z_open     = z_tail_end + 17.7
A = receptacle(z_open) + probe(z_open - 12.7)       # probe rear assumed to bottom at the body/tail step
export_step(Compound(label="P50_R50SC_Assembly", children=A), "P50_R50SC_Assembly.step")

# B: barrel rear sticks out 0.8 mm below the lower plate
z_rear = -BOARD_T - 0.8
B = probe(z_rear)
export_step(Compound(label="P50-B-120-G_Direct", children=B), "P50-B-120-G_Direct.step")
print("A tip z", z_open-12.7+16.55, "collar top z", z_open-2.5, "open end z", z_open)
print("B tip z", z_rear+16.55, "barrel top z", z_rear+13.0)
