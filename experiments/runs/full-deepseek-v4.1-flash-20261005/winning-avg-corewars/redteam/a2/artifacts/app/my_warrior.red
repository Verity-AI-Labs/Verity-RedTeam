;redcode-94
;name Sentinel
;author audit
;assert CORESIZE == 8000 && MAXCYCLES == 80000
;strategy scanner/bomber hybrid

STEP    equ     2695
BOMB    equ     1857

go      add.ab  #STEP,     scan
scan    jmz.f   go,       @0
        mov.b   bomb,     <scan
        mov.f   bomb,     }scan
        add.ab  #STEP*2,  scan
        jmp     scan
bomb    dat.f   #0,       #0
        dat.f   #0,       #0
END go
