# Divisão inteira por subtrações sucessivas.
# Invariante clássico de Hoare: a = q*b + r  e  0 <= r
# sigma: a=17, b=5
q = 0
r = a
while r >= b:
    r = r - b
    q = q + 1
