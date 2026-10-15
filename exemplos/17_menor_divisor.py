# Menor divisor maior que 1, com break. O salto vira uma bandeira no
# estado: a guarda passa a ser "não brk e d*d <= n", e o que vem depois
# do break no corpo só roda se a bandeira continuar falsa.
# sigma: n=91
# sigma: n=97
d = 2
achou = n
while d * d <= n:
    if n % d == 0:
        achou = d
        break
    d = d + 1
