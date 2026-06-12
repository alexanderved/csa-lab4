(defvar arr-len 0)
(defvar arr-cap 32)
(defvar A (allocate-array 32))
(defvar is-finished 0)

(defun read-array ()
    (declare (interrupt 0))
    (if is-finished
        0
        (if (< arr-len arr-cap)
            (let ((el (input 0)))
                (if (= el 0)
                    (setq is-finished 1)
                    (let ()
                        (write-array-element A arr-len el)
                        (setq arr-len (+ arr-len 1)))))
            (setq is-finished 1))))

(defun wait-array ()
    (if is-finished 0 (wait-array)))

(defun swap (arr i j)
    (let ((tmp (read-array-element arr i)))
        (write-array-element arr i (read-array-element arr j))
        (write-array-element arr j tmp)))

(defun bubble-sort-inner (arr j m)
    (if (< j m)
        (let ((a (read-array-element arr j)) (b (read-array-element arr (+ j 1))))
            (if (> a b)
                (swap arr j (+ j 1)))
            (bubble-sort-inner arr (+ j 1) m))))

(defun bubble-sort-outer (arr i n)
    (if (< i n)
        (let ()
            (bubble-sort-inner arr 0 (- n i))
            (bubble-sort-outer arr (+ i 1) n))))

(defun bubble-sort (arr len)
    (bubble-sort-outer arr 0 (- len 1)))

(wait-array)
(bubble-sort A arr-len)
(print-array A arr-len)