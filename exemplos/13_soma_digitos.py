# Soma dos dígitos na base 10.
# sigma: n=98765
soma = 0
m = n
while m > 0:
    soma = soma + m % 10
    m = m // 10
