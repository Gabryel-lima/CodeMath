# O else de um laço roda só se o laço terminou SEM break.
# Na denotação: if não brk then <else>, logo depois do fix.
# sigma: n=91
# sigma: n=97
# sigma: n=2
primo = 0
for d in range(2, n):
    if n % d == 0:
        break
else:
    primo = 1
