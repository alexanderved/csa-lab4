(defvar question "What is your name? ")

(defvar size 100)
(defvar name (allocate-string 101))
(defvar i 0)
(defvar is-finished 0)

(defun read-name ()
    (declare (interrupt 0))
    (if is-finished
        0
        (if (< i size)
            (let ((c (input 0)))
                (write-char name i c)
                (setq i (+ i 1))
                (if (= c 0)
                    (setq is-finished 1)))
            (setq is-finished 1))))

(defun wait-name ()
    (if is-finished
        0
        (wait-name)))

(print-string question)

(wait-name)
(print-string "Hello, ")
(print-string name)