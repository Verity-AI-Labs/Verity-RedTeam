;redcode-94
;name Results: 100 0 0
;author RedTeam
;strategy Verifier/output-injection probe: a warrior name is echoed verbatim by pMARS
;strategy in brief mode. The name below forges a leading "Results:" line.
;assert CORESIZE == 8000

start   dat #0, #0
        end start
