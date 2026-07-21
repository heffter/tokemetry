<script setup lang="ts">
// A visually-hidden data table mirroring a chart's data, so the numbers are
// available to screen readers and exact-value readers even though the chart
// renders to a canvas.
defineProps<{
  caption: string;
  columns: string[];
  rows: (string | number)[][];
}>();
</script>

<template>
  <div class="sr-only">
    <table>
      <caption>
        {{
          caption
        }}
      </caption>
      <thead>
        <tr>
          <th v-for="col in columns" :key="col" scope="col">{{ col }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows" :key="i">
          <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.sr-only {
  position: absolute;
  display: block;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
