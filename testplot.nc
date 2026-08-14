( Pen plotter shakedown - square + circle )
( Origin is machine 0,0 at the bottom-left corner. )
( Everything sits inside 20..120 mm on both axes, well inside A4. )
( Pen values: S400 = up, S1000 = down. Change both if yours differ. )

G21          ( mm )
G90          ( absolute )
G17          ( XY plane )
F2000        ( feed for pen-down moves )

M3 S400      ( pen UP )
G4 P0.3

( ---- 40 mm square, corner at 20,20 ---- )
G0 X20 Y20
M3 S1000
G4 P0.25
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
M3 S400
G4 P0.15

( ---- 40 mm circle, centre 100,40 ---- )
G0 X80 Y40
M3 S1000
G4 P0.25
G2 X80 Y40 I20 J0
M3 S400
G4 P0.15

( ---- home and park ---- )
G0 X0 Y0
M5           ( parks pen up )
M2
