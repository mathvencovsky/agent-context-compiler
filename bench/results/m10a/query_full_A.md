# M10a query cleaning, half A (139 tasks)

## Full pipeline (lambda 0.0), hit and tokens

| variant | hit 1000 | tokens 1000 | hit 2000 | tokens 2000 | hit 4000 | tokens 4000 | hit 8000 | tokens 8000 |
|---|---|---|---|---|---|---|---|---|
| current | 0.288 | 947 | 0.475 | 1,951 | 0.662 | 3,913 | 0.719 | 7,304 |
| strip | 0.288 | 951 | 0.504 | 1,962 | 0.655 | 3,917 | 0.748 | 7,296 |

strip vs current, paired (diff [95% CI], McNemar p):

- 1000: +0.000 [-0.022, +0.022], p=1.0000
- 2000: +0.029 [+0.000, +0.065], p=0.2188
- 4000: -0.007 [-0.050, +0.036], p=1.0000
- 8000: +0.029 [-0.007, +0.072], p=0.2891
