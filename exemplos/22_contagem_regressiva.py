# range com passo negativo: a guarda vira î > limite. E o range é
# avaliado UMA vez: o corpo aumentar k não muda quantas voltas o laço
# dá. Como o corpo mexe em k, a tradução congela o limite em î_fim
# antes do laço -- sem isso, a guarda releria o k novo e pararia cedo.
# sigma: n=5, k=0
total = 0
for i in range(n, k, -1):
    total = total + i
    k = k + 2
