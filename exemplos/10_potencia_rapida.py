# Exponenciação binária. Invariante: acc * base^exp = x^n (constante).
# sigma: x=3, n=5
acc = 1
base = x
exp = n
while exp > 0:
    if exp % 2 == 1:
        acc = acc * base
    base = base * base
    exp = exp // 2
