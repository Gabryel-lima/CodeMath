# Teste de primalidade por divisão tentativa, com for.
# sigma: n=91
# sigma: n=97
primo = 1
if n < 2:
    primo = 0
for d in range(2, n):
    if n % d == 0:
        primo = 0
