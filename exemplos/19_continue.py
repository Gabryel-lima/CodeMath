# continue pula o resto do corpo, mas não o incremento do contador:
# no açúcar do for, o "î := î + 1" fica fora do trecho protegido.
# sigma: n=12
soma = 0
for i in range(1, n + 1):
    if i % 3 == 0:
        continue
    soma += i
