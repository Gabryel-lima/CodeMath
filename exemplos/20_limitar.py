# Expressão condicional e funções embutidas (abs, min, max) viram
# notação matemática: |x|, min(a, b) e casos.
# sigma: x=-15, lo=0, hi=10
# sigma: x=7, lo=0, hi=10
dist = abs(x - lo)
limitado = min(max(x, lo), hi)
sinal = 1 if x > 0 else (-1 if x < 0 else 0)
