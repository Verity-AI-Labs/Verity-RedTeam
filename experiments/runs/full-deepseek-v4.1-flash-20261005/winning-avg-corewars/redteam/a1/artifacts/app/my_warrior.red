;redcode
;name AuditWarrior
;assert CORESIZE==8000
MOV.I  $4,     -1
ADD.AB #4,     -1
JMP.B  -2,     0
DAT.F  #0,     #0
END
