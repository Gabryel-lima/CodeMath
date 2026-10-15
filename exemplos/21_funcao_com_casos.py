# Função pura com variável local e return dentro de if: o corpo vira
# UMA expressão (a local é substituída, o if vira casos), e a chamada
# é expandida por substituição dos parâmetros.
# sigma: n=27
# sigma: n=8
def proximo(k):
    metade = k // 2
    if k % 2 == 0:
        return metade
    return 3 * k + 1

a = proximo(n)
b = proximo(a)
