# Diseño del agente

Este documento debe completarse **antes** de la implementación principal del agente.

Use sus propias palabras y notación. No reemplace este archivo por una transcripción
del enunciado. Las subsecciones existen para que no se le olvide una decisión;
usted decide el contenido.

El entorno, según las propiedades vistas en clase, es totalmente observable,
determinista, secuencial, estático, discreto y de agente único. Bajo esas
condiciones la solución es un **plan completo** y el marco correcto es la
búsqueda clásica. Justifique cada componente con ese marco (AIMA, cap. 3).

---

## Estado

### Definición formal

```text
s = ⟨posicion, bateria, inventario, puertas_abiertas, paneles_reparados, estaciones_activadas, items_en_suelo⟩
```

### Por qué cada variable es necesaria

- **batería** dos configuraciones que difieren en batería permiten acciones
  diferentes, ya que toda operación exige tener al menos la energía
  correspondiente a su costo.
- **posición** desde cada zona existen corredores, objetos,
  paneles, estaciones y cargadores diferentes. Por lo tanto, la posición
  condiciona directamente qué puede hacer el agente después.
- **inventario** dos configuraciones en la misma zona y con la misma batería
  pueden permitir acciones diferentes dependiendo de qué objetos lleva el
  robot. Una llave puede permitir abrir una puerta, una herramienta puede
  permitir reparar un panel y un material puede ser necesario para completar
  una reparación
- **puertas_desbloqueadas** abrir una puerta es un cambio permanente del
  entorno. Dos configuraciones que difieran tienen movimientos diferentes, ya
  que un corredor bloqueado solo se puede usar después de abrir su puerta.
- **paneles_reparados** reparar un panel modifica permanentemente el entorno.
  Dos configuraciones que difieran en el estado de un panel pueden permitir
  diferentes acciones futuras, porque un panel ya reparado no vuelve a
  repararse y su reparación puede ser requisito para activar una estación.
- **estaciones_activadas** el estado de las estaciones condiciona tanto las
  acciones futuras como la meta. Algunas estaciones requieren que otras ya
  estén ONLINE, por lo que dos configuraciones que difieran en las estaciones
  activadas pueden tener diferentes acciones disponibles. Además la misión
  termina cuando las estaciones indicadas en goal están activadas.


### Qué información se deriva y NO se almacena

- **peso de la carga** se calcula usando los objetos presentes y sus pesos
- **capacidad máxima de transprote** se obtiene de robot.cargo_capacity
- **bateria máxima** se obtiene de robot.battery_max
- **costo de las acciones** se obtienen de action_costs
- **costos y conexiones de corredores** se obtienen de corridors
- **ubicación de cargadores** se obtiene del escenario
- **llave requerida por cada puerta** se obtiene de doors
- **herramienta y material necesarios para cada panel** se obtienen de panels
- **dependencias de cada estacino** se obtienen de stations

### Qué pertenece al historial de búsqueda y no al estado físico

El estado físico es cómo está el mundo actualmente, entonces los que describen
cómo se llegó a ese estado son los que pertenecen al nodo de busqueda, como:
- costo_acumulado
- padre
- accion_anterior
- profundidad
- plan_recorrido

### Cuándo dos configuraciones son el mismo estado

Dos configuraciones pertenecen al mismo estado cuando coinciden en:
s = ⟨posicion, bateria, inventario, puertas_desbloqueadas, paneles_reparados, estaciones_activadas, items_en_suelo⟩

Es importante establecer que el orden en las colecciones no cree estados
diferentes, es necesario manejarlas de forma canónica.
Por eso se pueden utilizar estructuras inmutables como `frozenset` o tuplas
ordenadas para construir el hash. Las llaves y herramientas sí mantienen su id,
pero los materiales iguales no se distinguen con identificadores inventados.
Esto permite que == y hash representen correctamente la situación física y que
CLOSED pueda reconocer estados repetidos.

### Relevancia: objetos que ya no cambian el futuro

Las puertas, paneles y estaciones cambian de forma permanente, así:
CLOSED  -> OPEN
DAMAGED -> REPAIRED
OFFLINE -> ONLINE

Esto hace que ciertos objetos puedan dejar de tener utilidad a medida que avanza la misión.
- una llave deja de ser útil cuando ya no queda ninguna puerta cerrada que necesite esa llave
- una herramienta deja de ser util cuando ya no queda ningún panel pendiente que la necesite
- un material deja de ser útil cuando ya no queda ninguna reparación pendiente que consuma ese tipo.

Si el objeto todavía está dentro del inventario, no se puede ignorar
completamente porque sigue ocupando capacidad y podría ser necesario ejecutar
DROP. En cambio, si el objeto ya está fuera del inventario y no puede habilitar
ninguna acción futura, su ubicación exacta deja de ser importante para la
búsqueda. En ese caso el agente no vuelve a generar PICKUP para ese objeto.
Esto no elimina una solución óptima porque volver a recogerlo:
- agrega el costo de `PICKUP`
- consume batería
- ocupa capacidad
- no habilita ninguna acción útil

Por lo tanto, no puede mejorar un plan de costo mínimo.


## Acciones

| Acción | Precondiciones | Efectos | Costo |
| ------ | -------------- | ------- | ----- |
| `MOVER(destino)` | Existe corredor hacia `destino`; si tiene puerta, debe estar abierta; batería suficiente | cambia `posicion` y reduce `bateria` | costo del corredor |
| `RECOGER(item)` | el item está en la zona actual; cabe en la carga; sigue siendo relevante; batería suficiente | pasa el item del suelo al inventario | `action_costs.pickup` |
| `SOLTAR(item)` | el item está en inventario; batería suficiente; existe una necesidad de liberar capacidad | pasa el item del inventario al suelo | `action_costs.drop` |
| `ABRIR_PUERTA(puerta)` | robot en uno de sus extremos; puerta cerrada; llave requerida en inventario; batería suficiente | agrega la puerta a `puertas_abiertas` | `action_costs.interact` |
| `REPARAR(panel)` | robot en la zona; panel pendiente; herramienta y material requeridos en inventario; batería suficiente | repara el panel y consume el material | `action_costs.interact` |
| `ACTIVAR(estacion)` | robot en la zona; estación `OFFLINE`; dependencias cumplidas; batería suficiente | estación pasa a `ONLINE` | `action_costs.interact` |
| `RECARGAR(cargador)` | robot en la zona del cargador; batería menor al máximo; batería suficiente para pagar la recarga | restaura la batería al máximo | `action_costs.recharge` |

Para cualquier acción debe cumplirse además:
bateria >= costo_accion

En las acciones normales:
bateria' = bateria - costo_accion

Las llaves y herramientas son reutilizables, por lo que no desaparecen del
inventario después de utilizarlas. Los materiales sí son consumibles. Al
ejecutar REPARAR, se elimina una unidad del material requerido.

### `Applicable` interno vs legalidad del contrato

El contrato indica qué acciones acepta físicamente el simulador. `Applicable`,
en cambio, define qué sucesores vale la pena generar durante la búsqueda. Por
lo tanto: que una accion legal no significa que sea una accion que siempre debo
generar.

El caso principal es `DROP`. El simulador permite soltar cualquier objeto que
esté en el inventario. Si el agente generara todos esos `DROP` en todas las
zonas, cada objeto podría quedar en muchas posiciones diferentes. Eso aumenta
mucho el factor de ramificación sin necesariamente acercar al robot a la meta.
Por esta razón, `SOLTAR` se genera cuando la capacidad realmente está
bloqueando un `PICKUP` relevante. La condición general es: existe un item
relevante en la zona actual AND ese item no cabe con la carga actual.
Cuando esto ocurre, se consideran los objetos del inventario que pueden
soltarse para liberar el espacio necesario. No se limita cuál objeto se puede
soltar en ese momento; la búsqueda todavía puede decidir cuál alternativa
produce el mejor plan. La poda se hace sobre los `DROP` anticipados que todavía
no resuelven ningún problema de capacidad. Esto es válido porque el costo de
`MOVE` no depende del peso transportado mientras no se exceda la capacidad. Si
un objeto puede seguir en el inventario sin impedir ninguna acción, soltarlo
antes solo adelanta un costo que todavía no era necesario.

La búsqueda sigue conservando la decisión sobre qué objeto dejar cuando
realmente hace falta espacio. También se filtran `PICKUP` que ya no tienen
utilidad. Por ejemplo, si una llave únicamente servía para una puerta que ya
está abierta, no se vuelve a recoger.


## Modelo de transición

La función de transición se define como:
```text
s  --a-->  s'     solo si a ∈ Applicable(s)
```

Las transiciones principales son las siguientes.

- `MOVER`

```text
posicion' = destino
bateria' = bateria - costo_corredor
```

El inventario y el resto del entorno no cambian.

- `RECOGER`

Para una llave o herramienta:

```text
inventario' = inventario + item
items_en_suelo' = items_en_suelo - item
bateria' = bateria - costo_pickup
```

Para un material se mueve una unidad de ese tipo:

```text
cantidad_suelo'[zona][tipo] = cantidad_suelo[zona][tipo] - 1
cantidad_inventario'[tipo] = cantidad_inventario[tipo] + 1
bateria' = bateria - costo_pickup
```

- `SOLTAR`

Para un objeto único:

```text
inventario' = inventario - item
items_en_suelo' = items_en_suelo + (zona_actual, item)
bateria' = bateria - costo_drop
```

Para un material:

```text
cantidad_inventario'[tipo] = cantidad_inventario[tipo] - 1
cantidad_suelo'[zona][tipo] = cantidad_suelo[zona][tipo] + 1
bateria' = bateria - costo_drop
```

- `ABRIR_PUERTA`

```text
puertas_abiertas' = puertas_abiertas ∪ {puerta}
bateria' = bateria - costo_interact
```

La llave permanece en el inventario.

- `REPARAR`

```text
paneles_reparados' = paneles_reparados ∪ {panel}
cantidad_inventario'[material_requerido] = cantidad_inventario[material_requerido] - 1
bateria' = bateria - costo_interact
```

La herramienta permanece en el inventario.

- `ACTIVAR`

```text
estaciones_activadas' = estaciones_activadas ∪ {estacion}
bateria' = bateria - costo_interact
```

- `RECARGAR`

La acción solo es aplicable si:

```text
bateria < bateria_maxima AND bateria >= costo_recharge
```

El costo se debe poder pagar antes de ejecutar la recarga. Después:

```text
bateria' = bateria_maxima
```

Aunque la batería termine llena, el costo de `RECHARGE` sí se suma normalmente a `g(n)`.

Las variables no modificadas por una acción mantienen su valor. Después de cada
transición, el nuevo estado se canonicaliza antes de utilizarlo en `OPEN` o
`CLOSED`.

---

## Prueba de meta

```text
Goal(s) ⟺ goal.stations_online ⊆ estaciones_activadas
```
Es decir, todas las estaciones solicitadas en `goal` deben encontrarse `ONLINE`.

```text
Goal(s) ⟺ GENERATOR ∈ estaciones_activadas
AND
COMMAND ∈ estaciones_activadas
AND
ARTILLERY ∈ estaciones_activadas 
```

Las puertas abiertas y los paneles reparados no forman parte directa de la meta. Son estados 
intermedios necesarios para poder activar las estaciones. Tampoco se comprueba si el plan contiene
acciones específicas. Lo único importante es que el estado final cumpla las condiciones indicadas en 
`goal`.

---

## Función de costo
Cada nodo mantiene el costo total de la ruta utilizada para llegar hasta él.

```text
g(n) = 0
```
Para un nodo hijo:
```text
g(hijo) = g(padre) + costo(accion)
```
por lo tanto

```text
g(n) = Σ costo(ai)
```
donde cada costo(ai) corresponde al costo oficial definido en scenario.json.

Asimismo, los corredores tienen costos diferentes. Esto hace que minimizar la cantidad de pasos no sea lo mismo que minimizar el costo del plan. Una acción MOVE por un corredor de costo 12, por ejemplo, cuesta más que varias acciones de costo bajo juntas.


## Estrategia de búsqueda

Se utiliza **Uniform Cost Search (UCS)** implementado como Graph Search.

`OPEN` se maneja mediante una cola de prioridad ordenada por `g(n)`, por lo que
en cada iteración se extrae el nodo pendiente con menor costo acumulado.

UCS se elige porque los costos del problema no son uniformes: los corredores pueden tener costos
diferentes y las operaciones `PICKUP`, `DROP`, `INTERACT` y `RECHARGE` también tienen costos 
definidos por el escenario. Como el objetivo es encontrar el plan de menor costo acumulado, UCS se 
ajusta directamente al criterio de optimalidad del problema.

- **Completitud:** UCS es completo mientras cada estado tenga una cantidad finita de sucesores y los 
costos de las acciones sean positivos, con un costo mínimo `ε > 0`. Si no existe una solución, la 
búsqueda continúa hasta que `OPEN` queda vacío y retorna `FAILURE`.

- **Optimalidad:** la prueba de meta se realiza cuando el nodo se extrae de `OPEN`, no cuando se 
genera. Como UCS siempre extrae primero el nodo con menor `g(n)`, bajo sus condiciones de 
optimalidad el primer nodo meta extraído corresponde a una solución de costo mínimo. Comprobar la 
meta al generar un sucesor podría terminar la búsqueda antes de que se considere otra ruta de menor 
costo.

- **Costo de camino:** cada nodo mantiene su costo acumulado `g(n)`. La prioridad de `OPEN` depende 
de este valor y no de la profundidad ni del número de acciones realizadas. Si se encuentra una ruta 
de menor costo hacia un estado que todavía está pendiente en `OPEN`, se conserva la alternativa con 
menor `g(n)`.

- **Tiempo y espacio:** el costo computacional de UCS depende directamente del factor de 
ramificación generado por `Applicable`. En este problema ese factor no depende únicamente de los 
corredores disponibles, sino también de acciones como `PICKUP`, `DROP`, `OPEN_DOOR`, `REPAIR`, 
`ACTIVATE` y `RECHARGE`. `DROP` es especialmente importante porque puede crear muchas 
configuraciones distintas de ubicación de objetos. Por esta razón se restringen sucesores 
irrelevantes y se canonicalizan los estados. UCS también requiere memoria para mantener tanto `OPEN` 
como `CLOSED`.

- **Condiciones que pueden romper las garantías:** UCS deja de tener sus garantías normales si 
aparecen costos negativos o si no se cumplen las condiciones requeridas sobre los costos. La 
implementación también puede perder correctitud o volverse inviable si el estado omite información 
relevante, `==` y `hash` están mal definidos, no se manejan correctamente los estados repetidos, no 
se conserva la mejor ruta hacia un estado pendiente o `Applicable` genera demasiadas acciones 
irrelevantes.

- **CLOSED y estados repetidos:** Graph Search mantiene `CLOSED` con los estados que ya fueron 
expandidos. Para que funcione correctamente, `Estado` debe implementar `==` y `hash` utilizando su 
representación canónica. Así, dos configuraciones físicamente iguales se reconocen como el mismo 
estado aunque se hayan alcanzado por rutas diferentes. Esto también evita reexplorar ciclos y 
situaciones ya resueltas.

### Batería como recurso
La batería debe formar parte del estado porque afecta directamente qué 
acciones son aplicables. Sin embargo, el agente no debe conservar todas las llegadas posibles a una 
misma configuración del mundo si una de ellas es claramente peor.

Para comparar estas llegadas se usa una firma del estado sin incluir la batería:

```text
firma_mundo =
⟨posicion, inventario, items_en_suelo, puertas_abiertas, paneles_reparados, estaciones_activadas⟩
```
Si dos nodos tienen la misma firma_mundo, un nodo A domina a un nodo B cuando
```text
bateria_A >= bateria_B
AND
g(A) <= g(B)
```
Cuando esto ocurre, B debe descartarse porque llegó a la misma situación con menos o igual batería 
disponible y con un costo mayor o igual. Cualquier continuación posible desde B también puede 
realizarse desde A sin empeorar el costo.

Esta comparación es adicional al CLOSED normal, ya que la batería sigue formando parte del estado. 
El agente debe mantener para cada firma_mundo las combinaciones (bateria, g) no dominadas y eliminar 
o ignorar las que queden dominadas.

## Formulación y tamaño del espacio (obligatorio)

1. **¿Por qué «5 zonas, ~10 objetos, capacidad 3» puede generar millones de nodos en un UCS ingenuo?**

Porque el número de estados no depende únicamente de la posición del robot. También cambian la batería, el inventario, la ubicación de los objetos, las puertas abiertas, los paneles reparados y las estaciones activadas.

En particular, cada objeto que puede estar en distintas zonas o dentro del inventario introduce nuevas combinaciones. Si además se consideran diferentes niveles de batería y cambios permanentes del entorno, el espacio crece de forma combinatoria.

Por esta razón, el tamaño real del problema depende principalmente de cómo se representa el estado y de cuántos sucesores genera `Applicable`, no del número de zonas visibles en el mapa.

2. **¿Qué papel tiene `DROP` en esa explosión?**

`DROP` permite cambiar la ubicación de los objetos transportados. Si el agente genera `DROP` para cualquier objeto del inventario en cualquier zona, cada una de esas decisiones crea una nueva distribución posible de objetos.

Esas nuevas posiciones también pueden generar posteriormente nuevos `PICKUP`, aumentando todavía más el factor de ramificación.

Por eso `DROP` no debe generarse solamente porque sea legal según el simulador. El agente debe generarlo únicamente cuando sea necesario liberar capacidad para realizar un `PICKUP` relevante.

3. **¿Qué podas o abstracciones se aplican y por qué no pierden el óptimo?**

El agente aplica las siguientes restricciones:

- **`DROP` únicamente por necesidad de capacidad:** solo se genera cuando un objeto relevante de la zona actual no puede recogerse por falta de espacio. Esto no pierde el óptimo porque transportar un objeto no aumenta el costo de `MOVE`, por lo que un `DROP` anticipado puede posponerse hasta el momento en que realmente se necesite el espacio.
- **No generar `PICKUP` de objetos que ya no sean relevantes:** si una llave, herramienta o material ya no puede habilitar ninguna acción necesaria para completar la misión, no debe volver a recogerse.
- **No recoger materiales por encima de la demanda pendiente:** el agente debe calcular cuántas reparaciones restantes necesitan cada tipo de material y evitar transportar unidades que ya no puedan utilizarse.
- **Materiales equivalentes por tipo y cantidad:** no se deben crear identificadores individuales para materiales equivalentes. Esto evita distinguir estados que físicamente representan la misma situación.
- **Estados canónicos:** `inventario`, `items_en_suelo`, `puertas_abiertas`, `paneles_reparados` y `estaciones_activadas` deben tener una representación consistente para que `==` y `hash` reconozcan correctamente estados equivalentes
- **Graph Search con `CLOSED`:** ls estados ya expandidos no deben volver a expandirse.
- **Dominancia de batería:** para una misma configuración del mundo, se descartan las llegadas que tengan menor o igual batería y mayor o igual costo que otra llegada ya conocida.

Estas podas son eliminan únicamente acciones o estados que no pueden conducir a una solución de menor costo que otra alternativa conservada.

4. **¿Por qué no es solución subir la capacidad, bajar las estaciones o ignorar la batería?**

Porque esas modificaciones cambian el problema definido por `scenario.json` en lugar de mejorar el diseño del agente.

El agente debe respetar siempre:
- `cargo_capacity`;
- batería inicial y máxima;
- costos oficiales;
- recursos disponibles;
- puertas;
- paneles;
- estaciones;
- dependencias;
- condición de meta.

Podría haber un caso con otras instancias con valores diferentes. Por eso no se deben codificar soluciones específicas para el escenario visible ni modificar sus restricciones para reducir el espacio de búsqueda
La reducción del espacio debe hacerse mediante una mejor representación del estado, un `Applicable` más selectivo, estados canónicos, `CLOSED` y podas que conserven el plan óptimo.
