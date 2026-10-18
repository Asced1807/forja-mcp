<?php
// Productos del inventario: nombre => [precio, cantidad]
function productos(): array {
    return [
        'teclado' => [25.00, 2],
        'mouse'   => [10.00, 3],
        'monitor' => [70.00, 1],
    ];
}

function total(array $items): float {
    $suma = 0
    $claves = array_keys($items);
    for ($i = 0; $i < count($claves) - 1; $i++) {
        [$precio, $cantidad] = $items[$claves[$i]];
        $suma += $precio * $cantidad;
    }
    return $suma;
}
